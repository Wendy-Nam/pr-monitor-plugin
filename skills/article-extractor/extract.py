#!/usr/bin/env python3
"""
article-extractor
=================
뉴스 URL → 깨끗한 본문 + 메타데이터 JSON.

전략:
  1. 네이버 뉴스 → CSS 셀렉터 (가장 정확)
  2. Trafilatura (범용, F1 0.945)
  3. Jina Reader API 폴백

사용법:
  python3 extract.py <URL>
  python3 extract.py <URL> --output /path/to/file.json
  python3 extract.py <URL> --method trafilatura
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

try:
    import trafilatura
    from trafilatura.settings import use_config
except ImportError:
    print("pip install trafilatura --break-system-packages 필요", file=sys.stderr)
    sys.exit(2)

import requests

# ----- 설정 --------------------------------------------------------------
KST = timezone(timedelta(hours=9))
MIN_BODY_LENGTH = 500
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X 15_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# Trafilatura 설정 (precision 우선, recall 은 적당히)
TRAF_CONFIG = use_config()
TRAF_CONFIG.set("DEFAULT", "MIN_EXTRACTED_SIZE", "200")
TRAF_CONFIG.set("DEFAULT", "MIN_OUTPUT_SIZE", "200")


# ----- 결과 스키마 -------------------------------------------------------
def make_success(
    url: str,
    title: str,
    full_text: str,
    method: str,
    author: str | None = None,
    published_date: str | None = None,
    source_name: str | None = None,
    language: str = "ko",
) -> dict:
    paragraphs = [
        {"idx": i, "text": p.strip()}
        for i, p in enumerate(full_text.split("\n"), start=1)
        if p.strip() and len(p.strip()) > 20
    ]
    # 재번호 (20자 미만 단락 제거 후)
    for i, p in enumerate(paragraphs, start=1):
        p["idx"] = i

    return {
        "url": url,
        "fetched_at": datetime.now(KST).isoformat(),
        "title": title,
        "author": author,
        "published_date": published_date,
        "source_name": source_name or infer_source_name(url),
        "language": language,
        "full_text": full_text,
        "paragraphs": paragraphs,
        "word_count": len(full_text),
        "extraction_method": method,
    }


def make_error(url: str, error: str, **kwargs) -> dict:
    return {"url": url, "error": error, **kwargs}


# ----- 유틸 --------------------------------------------------------------
def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def infer_source_name(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    mapping = {
        "n.news.naver.com": "네이버뉴스",
        "news.naver.com": "네이버뉴스",
        "yna.co.kr": "연합뉴스",
        "yonhapnews.co.kr": "연합뉴스",
        "chosun.com": "조선비즈",
        "etnews.com": "전자신문",
        "zdnet.co.kr": "ZDNet Korea",
        "aitimes.com": "AI타임스",
        "reuters.com": "Reuters",
        "bloomberg.com": "Bloomberg",
        "ft.com": "Financial Times",
    }
    for domain, name in mapping.items():
        if domain in host:
            return name
    return host


def is_naver_news(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in ("n.news.naver.com", "news.naver.com")


# ----- 추출 전략 1: 네이버 뉴스 --------------------------------------------
def extract_naver(url: str) -> dict:
    """네이버 뉴스 CSS 셀렉터 기반 추출"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return make_error(url, "missing_dependency", hint="pip install beautifulsoup4")

    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return make_error(url, "http_error", status=r.status_code)
        soup = BeautifulSoup(r.text, "html.parser")

        body_el = soup.select_one("#dic_area") or soup.select_one("#articleBodyContents")
        title_el = soup.select_one(".media_end_head_title") or soup.select_one("h2.end_tit")
        date_el = soup.select_one(".media_end_head_info_datestamp_time")

        if not body_el:
            return make_error(url, "naver_body_not_found")

        # 광고/관련기사 등 제거
        for selector in [".link_news", ".reporter_area", ".media_end_linked_area",
                         ".vod_player_wrap", "script", "style", "ins"]:
            for el in body_el.select(selector):
                el.decompose()

        full_text = body_el.get_text(separator="\n", strip=True)
        full_text = re.sub(r"\n{3,}", "\n\n", full_text)

        if len(full_text) < MIN_BODY_LENGTH:
            return make_error(url, "too_short", length=len(full_text))

        title = title_el.get_text(strip=True) if title_el else ""
        pub_date = None
        if date_el and date_el.get("data-date-time"):
            pub_date = date_el["data-date-time"][:10]

        return make_success(
            url=url,
            title=title,
            full_text=full_text,
            method="naver_selector",
            published_date=pub_date,
            source_name="네이버뉴스",
        )
    except Exception as e:
        return make_error(url, "naver_exception", detail=str(e))


# ----- 추출 전략 2: Trafilatura ------------------------------------------
def extract_trafilatura(url: str) -> dict:
    """Trafilatura 기본 추출 (F1 0.945)"""
    try:
        downloaded = trafilatura.fetch_url(url, config=TRAF_CONFIG)
        if not downloaded:
            return make_error(url, "fetch_failed")

        # JSON 형식으로 메타데이터 포함 추출
        extracted = trafilatura.extract(
            downloaded,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=False,
            config=TRAF_CONFIG,
        )
        if not extracted:
            return make_error(url, "traf_extraction_empty")

        data = json.loads(extracted)
        full_text = data.get("text") or data.get("raw_text") or ""

        if len(full_text) < MIN_BODY_LENGTH:
            return make_error(url, "too_short", length=len(full_text))

        return make_success(
            url=url,
            title=data.get("title") or "",
            full_text=full_text,
            method="trafilatura",
            author=data.get("author"),
            published_date=(data.get("date") or "")[:10] or None,
            source_name=data.get("sitename"),
            language=data.get("language") or "ko",
        )
    except Exception as e:
        return make_error(url, "traf_exception", detail=str(e))


# ----- 추출 전략 3: Jina Reader ------------------------------------------
def extract_jina(url: str) -> dict:
    """Jina Reader API 폴백 (무료 티어)"""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        r = requests.get(
            jina_url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            return make_error(url, "jina_http_error", status=r.status_code)

        try:
            payload = r.json()
            full_text = payload.get("data", {}).get("content") or payload.get("content") or ""
            title = payload.get("data", {}).get("title") or payload.get("title") or ""
        except ValueError:
            # 일부 Jina 응답은 plain text markdown
            full_text = r.text
            title = ""

        if len(full_text) < MIN_BODY_LENGTH:
            return make_error(url, "too_short", length=len(full_text))

        return make_success(url=url, title=title, full_text=full_text, method="jina_reader")
    except Exception as e:
        return make_error(url, "jina_exception", detail=str(e))


# ----- 오케스트레이터 ----------------------------------------------------
def extract(url: str, method: str = "auto") -> dict:
    """전략에 따라 순차 시도"""
    if method == "naver" or (method == "auto" and is_naver_news(url)):
        result = extract_naver(url)
        if "error" not in result:
            return result
        if method == "naver":
            return result
        # auto 이고 naver 실패 → trafilatura 로 폴백

    if method in ("auto", "trafilatura"):
        result = extract_trafilatura(url)
        if "error" not in result:
            return result
        if method == "trafilatura":
            return result

    if method in ("auto", "jina"):
        return extract_jina(url)

    return make_error(url, "unknown_method", method=method)


# ----- 메인 --------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output", help="output JSON 경로 (기본 stdout)")
    parser.add_argument(
        "--method",
        choices=["auto", "naver", "trafilatura", "jina"],
        default="auto",
    )
    args = parser.parse_args()

    result = extract(args.url, method=args.method)

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"저장: {args.output}")
    else:
        print(output)

    # 에러는 exit code 1 (subagent 가 실패를 감지 가능)
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
