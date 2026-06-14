#!/usr/bin/env python3
"""
PR Monitor — URL Fetcher v2

config/sources.yaml 을 읽어:
  1. RSS (Google News + 직접 RSS) 폴링
  2. 제목 유사도 기반 클러스터링 (같은 사건 여러 매체 묶기)
  3. data/raw/urls-{date}.json 저장 (클러스터 구조 포함)

사용법:
  ./scripts/fetch-urls.py                   # 표준
  ./scripts/fetch-urls.py --hours 48        # 범위 확장
  ./scripts/fetch-urls.py --dry-run         # 저장 없이 요약
  ./scripts/fetch-urls.py --test-query "…"  # 단일 쿼리 테스트

의존성: feedparser python-dateutil pyyaml requests
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

# scripts/lib 서브패키지 import 를 위해 scripts/ 를 sys.path 에 추가
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import feedparser
    import yaml
    import requests
    from dateutil import parser as dateparser
    from dateutil import tz
except ImportError as e:
    print(f"ERROR: 의존성 누락 — {e}", file=sys.stderr)
    print("설치: uv pip install feedparser python-dateutil pyyaml requests", file=sys.stderr)
    print("      (선택, 최상의 RSS 복구를 위해) uv pip install lxml", file=sys.stderr)
    sys.exit(2)


# ============================================================
# 설정
# ============================================================

from lib.common import CONFIG_DIR, RAW_DIR, load_yaml, save_json

SOURCES_YAML = CONFIG_DIR / "sources.yaml"
OUTPUT_DIR = RAW_DIR

FETCHER_VERSION = "0.3.0"
USER_AGENT = "PRMonitor/0.2 (+news-monitor plugin)"
REQUEST_TIMEOUT = 15
KST = tz.gettz("Asia/Seoul")

# 섹션 내부 병렬 fetch worker 수.
# 네트워크 바운드라 10 내외가 적절. Google/Naver 레이트리밋 회피를 위해 너무 높지 않게.
DEFAULT_MAX_WORKERS = 8


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class Article:
    """단일 기사 (클러스터링 이전)."""
    url: str
    title: str
    source_name: str
    published_date: str
    language: str
    source_query: str | None = None
    source_tier: str = "tier1_google_news"
    author: str | None = None   # RSS 메타데이터에서 추출
    # 제목 기반 프리필터 결과
    title_boost: bool = False       # boost_keywords 매칭 여부
    priority_score: float = 0.0     # 정렬용 점수 (높을수록 우선)
    require_boost: bool = False     # 범용 RSS — boost 없으면 drop
    _title_norm: str = ""


@dataclass
class Cluster:
    """같은 사건을 보도한 기사들의 묶음."""
    cluster_id: str
    canonical_title: str
    canonical_url: str               # 대표 URL (첫 발행 매체의)
    canonical_source: str
    first_published: str
    article_count: int
    articles: list[dict] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    author_names: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    is_hot: bool = False             # threshold 이상 매체 보도
    any_boosted: bool = False        # 클러스터 내 기사 중 title_boost=True 가 하나라도 있으면
    max_priority_score: float = 0.0  # 클러스터 내 최고 priority_score


# ============================================================
# 공통 유틸
# ============================================================

def parse_published(entry: dict[str, Any]) -> dt.datetime | None:
    for field_name in ("published", "updated", "pubDate"):
        if value := entry.get(field_name):
            try:
                parsed = dateparser.parse(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                return parsed
            except (ValueError, TypeError):
                continue
    for field_name in ("published_parsed", "updated_parsed"):
        if value := entry.get(field_name):
            try:
                return dt.datetime(*value[:6], tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def is_within_window(published: dt.datetime | None, hours: int) -> bool:
    if published is None:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=hours)
    return published >= cutoff


def normalize_title(title: str) -> str:
    title = title.lower()
    # 매체명 꼬리 제거 (예: "... - 전자신문" → "...")
    title = re.sub(r"\s+[-|·]\s+.{1,20}$", "", title)
    title = re.sub(r"[^\w\s가-힣]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ============================================================
# 제목 기반 프리필터
# ============================================================

def _title_contains_any(title_lower: str, keywords: list[str]) -> list[str]:
    """title_lower 에 포함된 키워드 목록 리턴 (대소문자 구분 없이)."""
    hits = []
    for kw in keywords or []:
        if kw.lower() in title_lower:
            hits.append(kw)
    return hits


def apply_title_filter(articles: list['Article'],
                       title_filter_cfg: dict) -> tuple[list['Article'], dict]:
    """
    제목 기반 프리필터.

    Returns:
        (통과한 articles, 통계 dict)
    """
    if not title_filter_cfg.get("enabled", False):
        return articles, {"enabled": False}

    strong_exclude_kw = title_filter_cfg.get("strong_exclude_keywords", [])
    exclude_domains = set(
        d.lower().lstrip("www.")
        for d in title_filter_cfg.get("exclude_domains", [])
    )
    boost_kw = title_filter_cfg.get("boost_keywords", [])
    exclude_kw = title_filter_cfg.get("exclude_keywords", [])
    exceptions = title_filter_cfg.get("exclude_exceptions", [])

    stats = {
        "enabled": True,
        "input": len(articles),
        "domain_excluded": 0,
        "strong_excluded": 0,
        "boosted": 0,
        "excluded": 0,
        "neutral": 0,
        "excluded_samples": [],
    }

    kept: list[Article] = []

    for art in articles:
        title_lower = art.title.lower()

        # -1) 도메인 blocklist — 매체 전체가 저가치 (O(1) hash lookup)
        if exclude_domains:
            dom = extract_domain(art.url).lower().lstrip("www.")
            if dom in exclude_domains:
                stats["domain_excluded"] += 1
                if len(stats["excluded_samples"]) < 10:
                    stats["excluded_samples"].append({
                        "title": art.title[:70],
                        "matched": [dom],
                        "source": art.source_name,
                        "tier": "domain_block",
                    })
                continue

        # 0) strong_exclude — boost 보다 우선. 자사 이름이 있어도 drop.
        #    (단순 시세 commentary 는 자사 언급이어도 intel 가치 없음)
        strong_hits = _title_contains_any(title_lower, strong_exclude_kw)
        if strong_hits:
            stats["strong_excluded"] += 1
            if len(stats["excluded_samples"]) < 10:
                stats["excluded_samples"].append({
                    "title": art.title[:70],
                    "matched": strong_hits[:3],
                    "source": art.source_name,
                    "tier": "strong_exclude",
                })
            continue

        # 1) boost 매칭
        boost_hits = _title_contains_any(title_lower, boost_kw)
        if boost_hits:
            art.title_boost = True
            art.priority_score += 10.0
            # source_query 가 있으면 (= 검색 기반) 추가 가점
            if art.source_query:
                art.priority_score += 5.0
            stats["boosted"] += 1
            kept.append(art)
            continue

        # 2) exclude 매칭 (boost 없을 때만 체크)
        exclude_hits = _title_contains_any(title_lower, exclude_kw)
        if exclude_hits:
            # 예외 검사 — "시장 동향" 같은 것
            exception_hit = any(exc.lower() in title_lower for exc in exceptions)
            if exception_hit:
                stats["neutral"] += 1
                kept.append(art)
                continue

            stats["excluded"] += 1
            if len(stats["excluded_samples"]) < 10:
                stats["excluded_samples"].append({
                    "title": art.title[:70],
                    "matched": exclude_hits[:3],
                    "source": art.source_name,
                })
            continue

        # 3) 중립 — require_boost 소스면 drop, 아니면 통과
        if art.require_boost:
            stats.setdefault("require_boost_dropped", 0)
            stats["require_boost_dropped"] += 1
            if len(stats["excluded_samples"]) < 10:
                stats["excluded_samples"].append({
                    "title": art.title[:70],
                    "matched": ["require_boost"],
                    "source": art.source_name,
                    "tier": "require_boost",
                })
            continue
        stats["neutral"] += 1
        kept.append(art)

    stats["output"] = len(kept)
    return kept, stats


def build_google_news_url(query: str, hl: str = "en", gl: str = "US") -> str:
    encoded = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl={gl}&ceid={gl}:{hl}"



def extract_domain(url: str) -> str:
    """URL 에서 도메인 추출. 매체명 fallback 으로 사용."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


# ============================================================
# RSS 폴링
# ============================================================

def _fix_common_xml_errors(raw: bytes) -> bytes:
    """
    RSS XML 에 자주 섞이는 invalid 문자를 제거/치환.

    - 제어문자 (tab/newline/CR 제외) 제거: 많은 CMS 가 U+0001~U+0008 등을 흘림
    - ampersand (&) 가 엔티티 아닌 raw 로 있으면 &amp; 로 (XML 에러 최다 원인)
    - BOM 제거
    """
    # 1) BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    # 2) 제어문자 제거 (XML 1.0 허용: tab=0x09, LF=0x0A, CR=0x0D, >=0x20)
    #    바이트 레벨에서 처리
    allowed_control = {0x09, 0x0A, 0x0D}
    cleaned = bytearray()
    for byte in raw:
        if byte < 0x20 and byte not in allowed_control:
            continue
        cleaned.append(byte)
    raw = bytes(cleaned)

    # 3) 잘못된 ampersand 치환: &foo (엔티티 패턴 아님) → &amp;foo
    #    너무 공격적이면 제대로 된 엔티티도 깰 수 있어 조심.
    #    유명 엔티티 (&amp; &lt; &gt; &quot; &apos; &#xxx; &#xHHHH;) 는 유지.
    try:
        text = raw.decode("utf-8", errors="replace")
        # 엔티티 참조가 아닌 & 를 모두 &amp; 로
        text = re.sub(
            r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)",
            "&amp;",
            text,
        )
        raw = text.encode("utf-8")
    except Exception:
        pass  # 인코딩 실패는 무시, 원본 유지

    return raw


def _fetch_raw_bytes(url: str, timeout: int = REQUEST_TIMEOUT,
                     max_retries: int = 3) -> bytes | None:
    """
    requests 로 raw bytes 받기. 실패 시 None.

    Connection reset / 일시적 5xx 는 지수 백오프로 재시도.
    DNS 실패 / 4xx 는 즉시 포기.
    """
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT},
                             timeout=timeout, allow_redirects=True)
            # 4xx 는 재시도 의미 없음
            if 400 <= r.status_code < 500:
                return None
            r.raise_for_status()
            return r.content
        except requests.exceptions.ConnectionError:
            # Connection reset, DNS, etc. — 재시도할 가치 있음
            if attempt == max_retries - 1:
                return None
            time.sleep(backoff)
            backoff *= 2
        except requests.exceptions.Timeout:
            if attempt == max_retries - 1:
                return None
            time.sleep(backoff)
            backoff *= 2
        except Exception:
            # 기타 (SSL, HTTP 5xx 등) — 재시도 1회만
            if attempt > 0:
                return None
            time.sleep(backoff)
    return None


def _parse_with_fallback(raw: bytes, url: str, source_name: str) -> Any:
    """
    3단계 폴백:
      1) feedparser 기본 (raw bytes)
      2) _fix_common_xml_errors 적용 후 feedparser
      3) lxml recover 모드 + feedparser (XML 심각하게 깨진 경우)
    """
    # 1차 시도
    parsed = feedparser.parse(raw)
    if parsed.entries:
        return parsed

    # 2차: 일반적 에러 수리
    fixed = _fix_common_xml_errors(raw)
    parsed = feedparser.parse(fixed)
    if parsed.entries:
        print(f"  INFO [{source_name}] XML 1차 보정 후 {len(parsed.entries)}건 복구",
              file=sys.stderr)
        return parsed

    # 3차: lxml recover (설치되어 있으면)
    try:
        from lxml import etree
        parser = etree.XMLParser(recover=True, encoding="utf-8")
        root = etree.fromstring(fixed, parser=parser)
        if root is not None:
            reconstructed = etree.tostring(root, encoding="utf-8")
            parsed = feedparser.parse(reconstructed)
            if parsed.entries:
                print(f"  INFO [{source_name}] lxml recover 로 {len(parsed.entries)}건 복구",
                      file=sys.stderr)
                return parsed
    except ImportError:
        pass   # lxml 없으면 skip
    except Exception as e:
        print(f"  WARN [{source_name}] lxml recover 에러: {e}", file=sys.stderr)

    return parsed   # 빈 상태로 반환


def fetch_feed(url: str, source_name: str, language: str,
               source_query: str | None, hours: int, source_tier: str,
               max_items: int = 20) -> list[Article]:
    """
    RSS 피드 파싱. 망가진 XML 도 최대한 복구.

    단계:
      1) requests 로 raw bytes 받기
      2) _parse_with_fallback 으로 3단계 파싱 시도
      3) 실패하면 빈 리스트 + warn
    """
    # raw bytes 확보
    raw = _fetch_raw_bytes(url)
    if raw is None:
        # 네트워크 레벨 실패
        # feedparser 에 URL 직접 넘기는 최후 시도 (과거 동작 호환)
        try:
            parsed = feedparser.parse(url, agent=USER_AGENT,
                                      request_headers={"User-Agent": USER_AGENT})
        except Exception as e:
            print(f"  WARN fetch 실패 [{source_name}]: {e}", file=sys.stderr)
            return []
    else:
        parsed = _parse_with_fallback(raw, url, source_name)

    if not parsed.entries:
        if parsed.bozo:
            err = getattr(parsed, "bozo_exception", "unknown")
            print(f"  WARN 파싱 실패 [{source_name}]: {err}", file=sys.stderr)
        else:
            # 정상 파싱인데 entries=0 (주말/휴일 빈 피드 등 — 정상 케이스)
            pass
        return []

    articles: list[Article] = []
    for entry in parsed.entries[:max_items]:
        published = parse_published(entry)
        if not is_within_window(published, hours):
            continue
        url_val = entry.get("link", "").strip()
        title_val = entry.get("title", "").strip()
        if not url_val or not title_val:
            continue

        # RSS 에서 author 추출 시도
        author = entry.get("author") or entry.get("dc_creator") or None

        articles.append(Article(
            url=url_val,
            title=title_val,
            source_name=source_name,
            published_date=published.isoformat() if published else "",
            language=language,
            source_query=source_query,
            source_tier=source_tier,
            author=author,
        ))
    return articles


def _run_parallel(
    tasks: list[tuple[Callable[..., list[Article]], tuple, str]],
    max_workers: int,
) -> list[Article]:
    """섹션 내부 fetch 작업들을 ThreadPool 로 병렬 실행.

    tasks: [(fn, args_tuple, label), ...]
    """
    results: list[Article] = []
    if not tasks:
        return results
    # 작업 수보다 worker 많을 필요 없음
    workers = min(max_workers, len(tasks))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, *args): label for fn, args, label in tasks}
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                articles = fut.result()
            except Exception as e:
                print(f"  WARN {label} 실패: {e}", file=sys.stderr)
                continue
            results.extend(articles)
            print(f"  + {label}: {len(articles)}건", file=sys.stderr)
    return results


def _mark_require_boost(articles: list[Article], require: bool) -> list[Article]:
    """소스 설정의 require_boost 를 기사에 전파."""
    if require:
        for a in articles:
            a.require_boost = True
    return articles


def process_direct_rss_section(section_data: list[dict], hours: int,
                               source_tier: str,
                               max_workers: int = DEFAULT_MAX_WORKERS) -> list[Article]:
    tasks: list[tuple[Callable[..., list[Article]], tuple, str]] = []
    require_boost_map: dict[str, bool] = {}
    for feed_def in section_data:
        if not isinstance(feed_def, dict) or "url" not in feed_def:
            continue
        if feed_def.get("fetch_method") == "crawl":
            continue
        name = feed_def.get("name", feed_def["url"])
        require_boost_map[name] = feed_def.get("require_boost", False)
        tasks.append((
            fetch_feed,
            (feed_def["url"], name, feed_def.get("lang", "en"),
             None, hours, source_tier),
            name,
        ))
    results = _run_parallel(tasks, max_workers)
    for a in results:
        if require_boost_map.get(a.source_name, False):
            a.require_boost = True
    return results


def process_google_news_section(section_data: dict, hours_default: int,
                                time_window_override: bool = False,
                                max_workers: int = DEFAULT_MAX_WORKERS) -> list[Article]:
    if not isinstance(section_data, dict) or "queries" not in section_data:
        return []

    template = section_data.get("url_template",
        "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={gl}:{hl}")
    locale = section_data.get("default_locale", {"hl": "en", "gl": "US"})
    hl = locale.get("hl", "en")
    gl = locale.get("gl", "US")

    tasks: list[tuple[Callable[..., list[Article]], tuple, str]] = []
    for q_def in section_data["queries"]:
        if not isinstance(q_def, dict) or "query" not in q_def:
            continue

        tw_raw = q_def.get("time_window", "1d")
        if time_window_override:
            hours = hours_default
        else:
            try:
                days = int(re.match(r"(\d+)", tw_raw).group(1))
                hours = days * 24
            except (AttributeError, ValueError):
                hours = hours_default

        encoded = urllib.parse.quote_plus(q_def["query"])
        url = template.format(query=encoded, hl=hl, gl=gl)
        source_name = f"Google News [{q_def['query'][:40]}]"
        label = f"Google News [{q_def['query'][:50]}] (window={hours}h)"

        tasks.append((
            fetch_feed,
            (url, source_name, "ko" if hl == "ko" else "en",
             q_def["query"], hours, "tier1_google_news"),
            label,
        ))
    return _run_parallel(tasks, max_workers)


# ============================================================
# 클러스터링 (신규)
# ============================================================

def cluster_articles(articles: list[Article],
                     title_threshold: float = 0.75,
                     time_window_hours: int = 72,
                     cross_language: bool = False,
                     hot_threshold: int = 5) -> list[Cluster]:
    """제목 유사도 + 시간창 기준으로 클러스터링.

    알고리즘:
      1. 모든 기사를 URL 로 1차 중복 제거
      2. published_date 내림차순 정렬
      3. 각 기사에 대해 기존 클러스터와 비교
         - 언어 같거나 cross_language=True
         - 시간창 내
         - 제목 유사도 ≥ threshold
      4. 매칭되면 해당 클러스터에 추가, 아니면 신규 클러스터
      5. 대표 기사 선정 (first_published 기준)
    """
    # URL 1차 중복 제거
    seen_urls: set[str] = set()
    unique: list[Article] = []
    for art in articles:
        if art.url in seen_urls:
            continue
        seen_urls.add(art.url)
        art._title_norm = normalize_title(art.title)
        unique.append(art)

    # 발행일 내림차순
    unique.sort(key=lambda a: a.published_date, reverse=True)

    clusters: list[Cluster] = []
    window_sec = time_window_hours * 3600

    for art in unique:
        matched_cluster = None
        art_time = parse_published({"published": art.published_date})
        art_ts = art_time.timestamp() if art_time else 0

        for cl in clusters:
            # 언어 필터
            if not cross_language and art.language not in cl.languages:
                continue
            # 시간창 필터 (클러스터 first_published 기준)
            cl_time = parse_published({"published": cl.first_published})
            cl_ts = cl_time.timestamp() if cl_time else 0
            if abs(art_ts - cl_ts) > window_sec:
                continue
            # 제목 유사도 (클러스터 canonical 과 비교)
            cl_title_norm = normalize_title(cl.canonical_title)
            if title_similarity(art._title_norm, cl_title_norm) >= title_threshold:
                matched_cluster = cl
                break

        if matched_cluster:
            # 기존 클러스터에 추가
            matched_cluster.articles.append({
                "url": art.url,
                "title": art.title,
                "source_name": art.source_name,
                "author": art.author,
                "published": art.published_date,
                "language": art.language,
                "source_query": art.source_query,
                "source_tier": art.source_tier,
            })
            matched_cluster.article_count += 1
            if art.source_name not in matched_cluster.source_names:
                matched_cluster.source_names.append(art.source_name)
            if art.author and art.author not in matched_cluster.author_names:
                matched_cluster.author_names.append(art.author)
            if art.language not in matched_cluster.languages:
                matched_cluster.languages.append(art.language)
            # first_published 업데이트 (더 이른 게 들어오면)
            if art.published_date and art.published_date < matched_cluster.first_published:
                matched_cluster.first_published = art.published_date
                matched_cluster.canonical_title = art.title
                matched_cluster.canonical_url = art.url
                matched_cluster.canonical_source = art.source_name
            matched_cluster.is_hot = matched_cluster.article_count >= hot_threshold
        else:
            # 신규 클러스터
            cluster_id = f"c_{dt.datetime.now().strftime('%Y%m%d')}_{len(clusters)+1:03d}"
            clusters.append(Cluster(
                cluster_id=cluster_id,
                canonical_title=art.title,
                canonical_url=art.url,
                canonical_source=art.source_name,
                first_published=art.published_date,
                article_count=1,
                articles=[{
                    "url": art.url,
                    "title": art.title,
                    "source_name": art.source_name,
                    "author": art.author,
                    "published": art.published_date,
                    "language": art.language,
                    "source_query": art.source_query,
                    "source_tier": art.source_tier,
                }],
                source_names=[art.source_name],
                author_names=[art.author] if art.author else [],
                languages=[art.language],
                is_hot=False,
                any_boosted=art.title_boost,
                max_priority_score=art.priority_score,
            ))

    # 각 클러스터의 boost 플래그 통합 (멤버 추가로 변경됐을 수 있음)
    # → 클러스터에 추가된 멤버의 boost 도 반영하도록 후처리
    # (간단 구현: 클러스터별로 멤버 순회)
    article_lookup = {a.url: a for a in articles}
    for cl in clusters:
        max_score = 0.0
        any_boost = False
        for m in cl.articles:
            orig = article_lookup.get(m["url"])
            if orig:
                max_score = max(max_score, orig.priority_score)
                if orig.title_boost:
                    any_boost = True
        cl.any_boosted = any_boost
        cl.max_priority_score = max_score

    # 각 클러스터 내 기사를 발행순으로 정렬
    for cl in clusters:
        cl.articles.sort(key=lambda a: a.get("published", ""))

    # 클러스터 정렬 — 최종 기준:
    #   1) any_boosted (관심 키워드 매칭) 먼저
    #   2) is_hot (여러 매체 보도)
    #   3) max_priority_score (누적 가점)
    #   4) article_count (언급 매체 수)
    #   5) first_published 내림차순 (최신)
    clusters.sort(key=lambda c: (
        c.any_boosted,
        c.is_hot,
        c.max_priority_score,
        c.article_count,
        c.first_published,
    ), reverse=True)

    return clusters


# ============================================================
# 저장
# ============================================================

def clusters_to_dict(clusters: list[Cluster]) -> list[dict]:
    return [
        {
            "cluster_id": c.cluster_id,
            "canonical_title": c.canonical_title,
            "canonical_url": c.canonical_url,
            "canonical_source": c.canonical_source,
            "first_published": c.first_published,
            "article_count": c.article_count,
            "is_hot": c.is_hot,
            "source_names": c.source_names,
            "author_names": c.author_names,
            "languages": c.languages,
            "articles": c.articles,
        }
        for c in clusters
    ]


def load_sources_config() -> dict:
    if not SOURCES_YAML.exists():
        print(f"ERROR: {SOURCES_YAML} 없음", file=sys.stderr)
        sys.exit(2)
    return load_yaml(SOURCES_YAML)


def apply_caps_to_clusters(clusters: list[Cluster], max_total: int) -> list[Cluster]:
    """전체 클러스터 상한만 적용. 클러스터 내부 기사는 제한 없음."""
    return clusters[:max_total]


# ============================================================
# 메인
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="PR Monitor URL Fetcher v2")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-query", type=str,
                        help="Google News 단일 쿼리 테스트")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--hours-override", action="store_true")
    parser.add_argument("--no-cluster", action="store_true",
                        help="클러스터링 건너뛰고 flat 리스트로 저장")
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS,
                        help=f"섹션 내부 병렬 fetch worker 수 (default: {DEFAULT_MAX_WORKERS})")
    args = parser.parse_args()

    # 단일 쿼리 테스트 분기
    if args.test_query:
        url = build_google_news_url(args.test_query)
        print(f"Google News URL: {url}", file=sys.stderr)
        articles = fetch_feed(url, f"[TEST] {args.test_query}", "en",
                              args.test_query, args.hours, "tier1_google_news")
        for a in articles[:10]:
            print(f"  - [{a.published_date[:10]}] {a.title[:80]}")
            print(f"    {a.url}")
        print(f"\n총 {len(articles)}건", file=sys.stderr)
        return 0

    print(f"=== PR Monitor URL Fetcher v{FETCHER_VERSION} ===", file=sys.stderr)
    print(f"시간 범위: 최근 {args.hours}시간 "
          f"({'강제' if args.hours_override else 'per-query 설정 우선'})", file=sys.stderr)

    cfg = load_sources_config()
    settings = cfg.get("settings", {})
    max_total = settings.get("max_articles_per_day", 150)

    clustering_cfg = settings.get("clustering", {})
    cluster_enabled = clustering_cfg.get("enabled", True) and not args.no_cluster
    title_threshold = clustering_cfg.get("title_similarity_threshold", 0.75)
    time_window_cluster = clustering_cfg.get("time_window_hours", 72)
    cross_language = clustering_cfg.get("cross_language_cluster", False)
    hot_threshold = clustering_cfg.get("hot_cluster_threshold", 5)

    all_articles: list[Article] = []

    if "rss_feeds_global" in cfg:
        print("\n[1] 해외 직접 RSS (병렬)", file=sys.stderr)
        all_articles.extend(process_direct_rss_section(
            cfg["rss_feeds_global"], args.hours, "tier2_direct_rss",
            max_workers=args.workers))

    if "competitor_feeds" in cfg:
        print("\n[2] 경쟁사 공식 뉴스룸 (병렬)", file=sys.stderr)
        all_articles.extend(process_direct_rss_section(
            cfg["competitor_feeds"], args.hours, "tier2_direct_rss",
            max_workers=args.workers))

    if "google_news_queries_global" in cfg:
        print("\n[3] Google News 해외 (병렬)", file=sys.stderr)
        all_articles.extend(process_google_news_section(
            cfg["google_news_queries_global"], args.hours, args.hours_override,
            max_workers=args.workers))

    if "rss_feeds_korea" in cfg:
        print("\n[4] 국내 직접 RSS (병렬)", file=sys.stderr)
        all_articles.extend(process_direct_rss_section(
            cfg["rss_feeds_korea"], args.hours, "tier2_direct_rss",
            max_workers=args.workers))

    # [5] 한국어 검색 — Google News 한국어 로케일
    if "korea_search_queries" in cfg:
        print("\n[5] Google News 한국어 (병렬)", file=sys.stderr)
        all_articles.extend(process_google_news_section(
            cfg["korea_search_queries"], args.hours, args.hours_override,
            max_workers=args.workers))

    if "regulatory_sources" in cfg:
        print("\n[6] 규제/정책 (병렬)", file=sys.stderr)
        all_articles.extend(process_direct_rss_section(
            cfg["regulatory_sources"], args.hours, "tier2_direct_rss",
            max_workers=args.workers))

    total_before = len(all_articles)
    print(f"\n--- 수집 총 {total_before}건 ---", file=sys.stderr)

    # ============================================================
    # 제목 기반 프리필터 (LLM 이전, Google News 해석 이전)
    # 순서 중요: 해석은 URL 당 수 초 걸리는 비싼 작업이므로
    # 제목에서 드롭 가능한 것은 먼저 걸러서 해석 비용 절약.
    # title 은 Google News redirect URL 이어도 RSS entry 에 이미 있음.
    # ============================================================
    title_filter_cfg = settings.get("title_filters", {})
    # strong_exclude 는 keywords.yaml 이 단일 정본 (classify.py 와 동일 리스트 공유)
    kw_cfg = load_yaml(CONFIG_DIR / "keywords.yaml")
    title_filter_cfg["strong_exclude_keywords"] = kw_cfg.get("strong_exclude", [])
    if title_filter_cfg.get("enabled", False):
        all_articles, tf_stats = apply_title_filter(all_articles, title_filter_cfg)
        print(f"--- 프리필터: "
              f"domain_block {tf_stats.get('domain_excluded', 0)}, "
              f"strong_exclude {tf_stats.get('strong_excluded', 0)}, "
              f"exclude {tf_stats['excluded']}, "
              f"require_boost_drop {tf_stats.get('require_boost_dropped', 0)}, "
              f"boost {tf_stats['boosted']}, "
              f"neutral {tf_stats['neutral']} → 유지 {tf_stats['output']}건 ---",
              file=sys.stderr)
        if tf_stats.get("excluded_samples"):
            print("  [제외 샘플]", file=sys.stderr)
            for s in tf_stats["excluded_samples"][:5]:
                print(f"    - [{s['source']}] {s['title']} ← {s['matched']}", file=sys.stderr)

    # ============================================================
    # Google News redirect URL 해석 (HTTP 451 우회)
    # settings.gnews_url_resolution.enabled 로 토글. 기본 on.
    # 프리필터 이후 → 해석 대상이 최소화된 상태 (비용 최소).
    # ============================================================
    gnews_cfg = settings.get("gnews_url_resolution", {"enabled": True})
    if gnews_cfg.get("enabled", True):
        try:
            from lib.gnews_resolver import resolve_urls_in_articles, is_gnews_redirect
            to_resolve_count = sum(
                1 for a in all_articles if is_gnews_redirect(a.url)
            )
            if to_resolve_count:
                print(f"\n--- Google News URL 해석 중 ({to_resolve_count}건, 프리필터 통과분만) ---",
                      file=sys.stderr)
                stats = resolve_urls_in_articles(
                    all_articles,
                    interval_seconds=gnews_cfg.get("interval_seconds", 0.3),
                    max_consecutive_failures=gnews_cfg.get(
                        "max_consecutive_failures", 8),
                    max_workers=gnews_cfg.get("max_workers", 5),
                )
                print(f"  캐시 {stats.cache_hits} / 해석 {stats.resolved} "
                      f"/ 실패 {stats.failed}"
                      f"{' (조기종료)' if stats.aborted_early else ''}",
                      file=sys.stderr)
                # 해석 실패 URL 은 downstream 에서 어차피 추출 실패하므로
                # 여기서 미리 제거 (비용·시간 절약)
                if gnews_cfg.get("drop_unresolved", True):
                    before_drop = len(all_articles)
                    all_articles = [
                        a for a in all_articles if not is_gnews_redirect(a.url)
                    ]
                    dropped = before_drop - len(all_articles)
                    if dropped:
                        print(f"  미해석 Google News URL {dropped}건 drop "
                              "(drop_unresolved=true)", file=sys.stderr)
        except ImportError as e:
            print(f"  WARN gnews_resolver import 실패: {e}", file=sys.stderr)

    # 클러스터링
    if cluster_enabled:
        clusters = cluster_articles(
            all_articles,
            title_threshold=title_threshold,
            time_window_hours=time_window_cluster,
            cross_language=cross_language,
            hot_threshold=hot_threshold,
        )
        total_clusters = len(clusters)
        total_in_clusters = sum(c.article_count for c in clusters)
        hot_count = sum(1 for c in clusters if c.is_hot)
        print(f"--- 클러스터링 후: {total_clusters}개 클러스터 "
              f"(내부 기사 {total_in_clusters}건, Hot {hot_count}개) ---",
              file=sys.stderr)

        # 상한
        clusters = apply_caps_to_clusters(clusters, max_total)
        final_count = len(clusters)
    else:
        clusters = []
        final_count = total_before

    now_kst = dt.datetime.now(KST)
    date_str = args.date or now_kst.strftime("%Y-%m-%d")

    if cluster_enabled:
        payload = {
            "fetched_at": now_kst.isoformat(),
            "fetcher_version": FETCHER_VERSION,
            "source_strategy": "multi_source_with_clustering",
            "time_window_hours": args.hours,
            "total_raw_articles": total_before,
            "total_clusters": len(clusters),
            "hot_clusters": sum(1 for c in clusters if c.is_hot),
            "clustering_config": {
                "title_threshold": title_threshold,
                "time_window_hours": time_window_cluster,
                "cross_language": cross_language,
                "hot_threshold": hot_threshold,
            },
            "clusters": clusters_to_dict(clusters),
        }
    else:
        # no-cluster 모드
        seen = set()
        flat_articles = []
        for a in all_articles:
            if a.url in seen:
                continue
            seen.add(a.url)
            d = asdict(a)
            d.pop("_title_norm", None)
            flat_articles.append(d)
        payload = {
            "fetched_at": now_kst.isoformat(),
            "fetcher_version": FETCHER_VERSION,
            "source_strategy": "multi_source_no_clustering",
            "time_window_hours": args.hours,
            "total_articles": len(flat_articles),
            "urls": flat_articles[:max_total],
        }

    if args.dry_run:
        print("\n=== DRY RUN 요약 ===")
        if cluster_enabled:
            print(f"수집: {total_before} → 클러스터 {len(clusters)}개 "
                  f"(Hot {payload['hot_clusters']}개)")
            print(f"\n상위 5 클러스터:")
            for c in clusters[:5]:
                hot_flag = "🔥" if c.is_hot else "  "
                print(f"  {hot_flag} [{c.article_count}건] {c.canonical_title[:60]}")
                print(f"      매체: {', '.join(c.source_names[:5])}")
                if c.author_names:
                    print(f"      기자: {', '.join(c.author_names[:5])}")
        else:
            print(f"수집: {total_before} → 저장 {len(payload['urls'])}")
        return 0

    output_file = OUTPUT_DIR / f"urls-{date_str}.json"
    save_json(output_file, payload)

    print(f"\n✅ 저장: {output_file}", file=sys.stderr)
    if cluster_enabled:
        print(f"   {len(clusters)}개 클러스터, Hot {payload['hot_clusters']}개",
              file=sys.stderr)
    else:
        print(f"   {len(payload['urls'])}건", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
