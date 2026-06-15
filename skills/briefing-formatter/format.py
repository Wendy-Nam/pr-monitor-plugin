#!/usr/bin/env python3
from __future__ import annotations
"""
briefing-formatter v3
======================
insight-synthesizer JSON → 인사이트 뉴스레터 HTML.
결정론적. LLM 호출 없음.

사용법:
  python3 format.py \
    --input  data/processed/newsletter-briefing-2026-06-05.json \
    --date   2026-06-05 \
    --output data/output/newsletter-report-2026-06-05.html \
    [--collection-start 2026-06-03] \
    [--foreign 25] [--domestic 10]

입력 JSON (insight-synthesizer 출력):
  {
    "date": "2026-06-05",
    "tldr": "...",
    "insights": [
      {
        "title": "...",
        "implication": "...",
        "observation": "...",
        "self_context": "...",          // 또는 self_context_crossing
        "facts": [                      // 또는 supporting_facts
          {
            "text": "FANUC: ...",       // 또는 summary
            "source_name": "Machinery", // 또는 source
            "source_date": "2026-06-04",// 또는 date
            "source_url": "https://...",// 또는 url
            "source_title": "...",      // 선택: 원문 기사 제목
            "category": "industrial_robot"  // 또는 from_category
          }
        ]
      }
    ],
    "category_summary": [
      {
        "category_id": "humanoid",
        "category_name": "Humanoid",
        "color": "#7c3aed",
        "summary": "서술형 요약...",
        "sources": [
          {"name": "이데일리", "date": "2026-06-05", "url": "https://..."}
        ]
      }
    ],
    "all_sources": [                    // 선택: 수집된 전체 기사 목록
      {                                 // 인사이트/카테고리에 미인용된 기사도 출처 목록에 포함
        "url": "https://...",
        "name": "매체명",
        "date": "2026-06-04",
        "title": "기사 제목",
        "summary": "한줄 요약",
        "category": "humanoid"
      }
    ]
  }
"""

import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# 도메인팩 로더 — config/<name>.yaml → config-templates/<name>.yaml 순으로 읽는다.
# (file: <root>/skills/briefing-formatter/format.py → dirname×3 == 플러그인 루트, prmonitor 패키지 위치)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import domainpack


# ── 카테고리 색상 / 클래스 (도메인팩 categories.yaml 에서 구성) ──
_CAT_PACK = domainpack.load_pack("categories")
_CAT_DEFS = _CAT_PACK.get("categories", {})
_CAT_FALLBACKS = _CAT_PACK.get("fallbacks", {})

CAT_COLORS = {
    cid: _CAT_DEFS[cid]["color"]
    for cid in _CAT_PACK.get("order", [])
}
CAT_DOT_CLASSES = {
    cid: _CAT_DEFS[cid]["dot_class"]
    for cid in _CAT_PACK.get("order", [])
}
# fallback 카테고리(예: self·industrial_robot 등 도메인팩별)도 color/dot_class 가 있으면 병합.
# 키를 하드코딩하지 않는다 — 도메인팩에 없으면 그냥 건너뛴다.
for _fid, _fdef in _CAT_FALLBACKS.items():
    if isinstance(_fdef, dict):
        if _fdef.get("color"):
            CAT_COLORS.setdefault(_fid, _fdef["color"])
        if _fdef.get("dot_class"):
            CAT_DOT_CLASSES.setdefault(_fid, _fdef["dot_class"])

CAT_ORDER = list(_CAT_PACK.get("order", []))

# ── 브랜딩 문자열 (도메인팩 branding.yaml 에서 구성) ──
# 헤더/푸터를 하드코딩하지 않고 도메인팩에서 읽는다. 실제 값은 도메인팩에서 오며,
# 폴백은 최후의 중립 기본값(특정 조직 브랜딩 없음).
# 브랜딩: branding.yaml 값 우선, 비었거나 예시 자리표시면 회사명에서 파생(domainpack.branding).
HTML_HEADER_NEWSLETTER = domainpack.branding("html_header_newsletter")
HTML_FOOTER = domainpack.branding("html_footer")

# ── CSS ────────────────────────────────────────────────────────
CSS = """\
  @import url("https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;800&display=swap");
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background-color: #ffffff !important;
    font-family: 'Noto Sans KR', -apple-system, 'Pretendard', sans-serif;
    color: #1c1917;
    background: #fafaf9;
    line-height: 1.7;
    max-width: 740px;
    margin: 0 auto;
    padding: 40px 28px;
  }

  /* ── Header ── */
  .brand { font-size: 11px; font-weight: 700; color: #78716c; letter-spacing: 3px; margin-bottom: 6px; }
  h1 { font-size: 26px; font-weight: 800; letter-spacing: -0.5px; color: #1c1917; }
  .meta-line { font-size: 13px; color: #78716c; margin-top: 4px; margin-bottom: 20px; }

  /* ── TL;DR ── */
  .tldr {
    background: #fff;
    border: 1px solid #e7e5e4;
    border-left: 4px solid #1c1917;
    border-radius: 0 8px 8px 0;
    padding: 18px 22px;
    margin-bottom: 36px;
    font-size: 15px;
    color: #292524;
  }
  .tldr-lbl { font-size: 11px; font-weight: 800; letter-spacing: 2px; color: #a8a29e; margin-bottom: 8px; }

  /* ── Summary blocks ── */
  .summary-block {
    border-left: 4px solid #e7e5e4;
    background: #fff;
    border-radius: 0 8px 8px 0;
    padding: 14px 18px;
    margin-bottom: 12px;
    border: 1px solid #e7e5e4;
    border-left-width: 4px;
  }
  .summary-block .sb-hd {
    display: flex; align-items: center;
    font-size: 12px; font-weight: 700; color: #57534e;
    margin-bottom: 8px;
  }
  .summary-block .sb-hd .cat-dot { margin-right: 10px; }
  .summary-block p { font-size: 14px; color: #292524; line-height: 1.65; }
  .summary-block p + p { margin-top: 18px; }

  /* ── Section heading ── */
  h2 {
    font-size: 17px; font-weight: 800;
    color: #1c1917;
    margin: 40px 0 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #1c1917;
    letter-spacing: -0.3px;
  }

  /* ── Category group in headlines ── */
  .hl-group { margin-bottom: 22px; }
  .hl-group-hd {
    display: flex; align-items: center; gap: 8px;
    font-size: 12px; font-weight: 700; color: #57534e;
    margin-bottom: 8px;
  }
  .cat-dot {
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
  }
  .hl-group ul { list-style: none; }
  .hl-group li {
    font-size: 14px;
    padding: 7px 0;
    border-bottom: 1px solid #f5f5f4;
    display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
    color: #292524;
  }
  .hl-group li:last-child { border-bottom: none; }

  /* ── Source chip ── */
  .src-chip {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11px; font-weight: 600;
    background: #f5f5f4; color: #78716c;
    padding: 2px 9px 2px 7px; border-radius: 20px;
    text-decoration: none; border: 1px solid #e7e5e4;
    white-space: nowrap; flex-shrink: 0;
    transition: background 0.15s;
    margin-right: 6px; margin-bottom: 4px;
  }
  .src-chip:hover { background: #e7e5e4; color: #44403c; }
  .src-chip::before { content: '↗'; font-size: 10px; }

  /* ── Insight card ── */
  .insight {
    background: #fff;
    border: 1px solid #e7e5e4;
    border-radius: 10px;
    margin-bottom: 28px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
  }
  .insight-hd {
    background: #1c1917;
    color: #fff;
    padding: 14px 22px;
    font-size: 15px; font-weight: 700;
    line-height: 1.6;
  }
  .insight-num { color: #fbbf24; margin-right: 8px; }
  .insight-body { padding: 16px 20px; }

  .insight-sec { margin-bottom: 16px; }
  .insight-sec:last-child { margin-bottom: 0; }
  .sec-lbl {
    font-size: 11px; font-weight: 800; letter-spacing: 1.5px;
    margin-bottom: 4px;
  }
  .lbl-imp { color: #57534e; }
  .lbl-obs { color: #57534e; }
  .lbl-ctx { color: #57534e; }
  .lbl-fact { color: #a8a29e; }

  .insight-sec p { font-size: 14px; color: #292524; line-height: 1.65; }
  .insight-sec p + p { margin-top: 10px; }

  /* ── Fact list ── */
  .fact-list { list-style: none; margin-top: 8px; }
  .fact-item {
    font-size: 13px; color: #44403c;
    padding: 8px 0 8px 14px;
    border-left: 3px solid #e7e5e4;
    margin-bottom: 6px;
    display: flex; align-items: flex-start; gap: 10px;
    flex-wrap: wrap;
  }
  .fact-text { flex: 1; min-width: 0; line-height: 1.6; }

  /* ── Source appendix ── */
  .src-list { list-style: none; counter-reset: src; }
  .src-list li {
    counter-increment: src;
    font-size: 13px; color: #57534e;
    padding: 7px 0;
    border-bottom: 1px solid #f5f5f4;
    display: flex; gap: 10px; align-items: baseline;
  }
  .src-list li::before {
    content: counter(src);
    font-size: 11px; font-weight: 700;
    color: #a8a29e;
    background: #f5f5f4;
    border-radius: 50%;
    width: 20px; height: 20px;
    display: inline-flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .src-list a { color: #2563eb; text-decoration: none; font-weight: 600; }
  .src-list a:hover { text-decoration: underline; }
  .src-list .src-title { color: #78716c; font-size: 12px; }

  /* ── Footer ── */
  .footer {
    margin-top: 44px; padding-top: 16px;
    border-top: 1px solid #e7e5e4;
    font-size: 11px; color: #a8a29e;
    line-height: 1.65;
  }

  /* color tokens */
  .c-humanoid  { background: #7c3aed; }
  .c-industrial { background: #dc2626; }
  .c-cobot     { background: #2563eb; }
  .c-platform  { background: #059669; }
  .c-amr       { background: #d97706; }
  .c-funding   { background: #6b7280; }

  @media (max-width: 600px) {
    body { padding: 20px 14px !important; }
    h1   { font-size: 22px !important; }
    .insight-hd  { padding: 12px 16px !important; font-size: 14px !important; }
    .insight-body { padding: 16px !important; }
    .summary-block { padding: 12px 14px !important; }
    .footer { font-size: 10px !important; }
    .fact-item { flex-direction: column !important; gap: 4px !important; }
  }"""


# ── 날짜 유틸 ─────────────────────────────────────────────────
DOW_KO = ["월", "화", "수", "목", "금", "토", "일"]

def format_date_ko(date_str: str) -> str:
    """'2026-06-05' → '2026년 06월 05일 (목)'"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.year}년 {d.month:02d}월 {d.day:02d}일 ({DOW_KO[d.weekday()]})"

def collection_days(start: str, end: str) -> int:
    if not start:
        return 0
    return (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days


# ── 매체명 매핑 (도메인→한국어) ────────────────────────────────
_MEDIA_MAP: dict[str, str] = {}

def load_media_mapping():
    global _MEDIA_MAP
    if _MEDIA_MAP:
        return
    candidates = [
        Path("config/media-mapping.yaml"),
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "media-mapping.yaml",
    ]
    for c in candidates:
        if c.exists():
            try:
                import yaml
                with open(c, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                _MEDIA_MAP = data.get("mapping", {})
            except Exception:
                pass
            break

def resolve_media_name(domain_or_name: str) -> str:
    """도메인이면 매체명으로 변환. 이미 매체명이면 그대로."""
    if not domain_or_name:
        return domain_or_name
    # 도메인에서 선행 m. www. 제거 후 매핑 조회 (daum.net 등 중간 m. 보호)
    clean = domain_or_name
    if clean.startswith("www."):
        clean = clean[4:]
    if clean.startswith("m."):
        clean = clean[2:]
    if clean in _MEDIA_MAP:
        return _MEDIA_MAP[clean]
    if domain_or_name in _MEDIA_MAP:
        return _MEDIA_MAP[domain_or_name]
    return domain_or_name


# ── 경쟁사 별칭 (헤드라인 그룹 검증용) ────────────────────────
_COMPETITOR_ALIASES: dict[str, list[str]] = {}

def load_competitor_aliases():
    global _COMPETITOR_ALIASES
    if _COMPETITOR_ALIASES:
        return
    candidates = [
        Path("config/company-profile.yaml"),
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "company-profile.yaml",
    ]
    for c in candidates:
        if c.exists():
            try:
                import yaml
                with open(c, encoding="utf-8") as f:
                    profile = yaml.safe_load(f) or {}
                for comp in profile.get("competitors", []):
                    name = comp.get("name", "")
                    if name:
                        _COMPETITOR_ALIASES[name] = [name] + comp.get("aliases", [])
            except Exception:
                pass
            break


def competitor_in_text(group_name: str, text: str) -> bool:
    """헤드라인 텍스트에 해당 경쟁사명(별칭 포함)이 실제로 등장하는지.

    경쟁사 그룹은 기사 주체가 그 회사일 때만 의미 — 그룹명이 텍스트에 없으면
    오배정(리드 비교 언급 등)이므로 카테고리로 재배정한다. 짧은 약어는 단어 경계.
    """
    import re as _re
    text_l = text.lower()
    for n in _COMPETITOR_ALIASES.get(group_name, [group_name]):
        n_l = n.lower()
        if _re.fullmatch(r"[a-z0-9.\-]{1,4}", n_l):
            if _re.search(r"(?<![a-z0-9])" + _re.escape(n_l) + r"(?![a-z0-9])", text_l):
                return True
        elif n_l in text_l:
            return True
    return False


# ── 자사명 필터 (안전장치) ────────────────────────────────────
_SELF_BLOCKLIST: list[str] = []

def load_self_blocklist(company_profile_path: str | None = None):
    global _SELF_BLOCKLIST
    if _SELF_BLOCKLIST:
        return
    if not company_profile_path:
        candidates = [
            Path("config/company-profile.yaml"),
            Path(__file__).resolve().parent.parent.parent.parent / "config" / "company-profile.yaml",
        ]
        for c in candidates:
            if c.exists():
                company_profile_path = str(c)
                break
    if company_profile_path and Path(company_profile_path).exists():
        try:
            import yaml
            with open(company_profile_path, encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
            company = profile.get("company", {})
            name = company.get("name", "")
            if name:
                _SELF_BLOCKLIST.extend([name.lower(), name.lower().replace(" ", "")])
                # 별칭이 company-profile.yaml 에 명시돼 있으면 그것을 우선 사용.
                aliases = company.get("aliases") or []
                if aliases:
                    for a in aliases:
                        if a:
                            _SELF_BLOCKLIST.append(a.lower())
                else:
                    # 폴백: aliases 미정의 시 회사명 앞부분(제목에서 잘리는 축약형 후보)만
                    # 추가. 도메인 의존 형태소 치환·고정 리터럴 없이 접미사 비의존으로 동작.
                    if len(name) > 5:
                        _SELF_BLOCKLIST.append(name[:5].lower())
        except Exception:
            pass
    # pr-queries.yaml 의 self_aliases 도 읽어 영문 별칭 커버 (company-profile aliases 미정의 시 보완)
    pr_q_candidates = [
        Path("config/pr-queries.yaml"),
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "pr-queries.yaml",
    ]
    for pq in pr_q_candidates:
        if pq.exists():
            try:
                import yaml as _yaml
                pq_data = _yaml.safe_load(pq.read_text(encoding="utf-8")) or {}
                for alias in pq_data.get("self_aliases", []):
                    kw = str(alias).lower()
                    if kw and kw not in _SELF_BLOCKLIST:
                        _SELF_BLOCKLIST.append(kw)
            except Exception:
                pass
            break

def _is_self_mention(title: str, summary: str) -> bool:
    if not _SELF_BLOCKLIST:
        return False
    text = (title + " " + summary).lower()
    return any(kw in text for kw in _SELF_BLOCKLIST)


def _is_korean_media(source_name: str, url: str = "") -> bool:
    if any('가' <= ch <= '힣' for ch in source_name):
        return True
    return bool(url and (".kr/" in url or url.rstrip("/").endswith(".kr")))


# ── 소스 레지스트리 ───────────────────────────────────────────
class SourceRegistry:
    def __init__(self):
        self._entries: list[dict] = []
        self._url_to_num: dict[str, int] = {}

    def register(self, url: str, name: str, date: str,
                 summary: str = "", category: str = "",
                 title: str = "") -> int | None:
        if _is_self_mention(title, summary):
            return None
        # 블로그/비공식 채널 제외
        _BLOG_DOMAINS = ["tistory.com", "blog.naver.com", "brunch.co.kr", "medium.com/@", "velog.io", "notion.so"]
        if url and any(bd in url for bd in _BLOG_DOMAINS):
            return None
        # URL 없는 소스도 title 기반 키로 등록 (출처 목록에 번호 배정, 링크 없음)
        key = url if (url and url != "#") else f"_nourl_:{name}:{date}:{title or name}"
        if key in ("_nourl_:::", "_nourl_::"):
            return None  # name/title/date 모두 없으면 skip
        if key in self._url_to_num:
            return self._url_to_num[key]
        # 제목 기반 중복 제거 (같은 기사 다른 URL / 같은 도메인 유사 제목)
        import re as _re
        def _title_key(t):
            return _re.sub(r'[\s\W]+', '', t.strip().lower())[:25] if t else ""
        def _domain(u):
            try: return urlparse(u).netloc.replace("www.", "").replace("m.", "")
            except: return ""
        norm_key = _title_key(title)
        this_domain = _domain(url)
        if norm_key:
            for e in self._entries:
                e_key = _title_key(e.get("title", ""))
                e_domain = _domain(e.get("url", ""))
                # 같은 도메인 + 제목 앞부분 동일 → 중복
                if e_domain and e_domain == this_domain and e_key and norm_key[:15] == e_key[:15]:
                    self._url_to_num[key] = e["num"]
                    return e["num"]
                # 다른 도메인이라도 제목 거의 동일 → 중복
                if e_key and norm_key == e_key:
                    self._url_to_num[key] = e["num"]
                    return e["num"]
        # summary 없으면 title 폴백
        if not summary and title:
            summary = title
        num = len(self._entries) + 1
        self._entries.append({
            "num": num, "url": url, "name": name,
            "date": date, "summary": summary,
            "category": category, "title": title,
        })
        self._url_to_num[key] = num
        return num

    def renumber_by_category(self, cat_order: list[str]):
        """등록 완료 후, 카테고리 순서대로 재넘버링. 출처 목록 표시 순서와 번호가 일치하게."""
        cat_groups: dict[str, list] = {}
        for e in self._entries:
            cat_groups.setdefault(e.get("category", "기타"), []).append(e)

        order = cat_order + [k for k in cat_groups if k not in cat_order]
        new_entries = []
        num = 1
        old_to_new: dict[int, int] = {}
        for cat_id in order:
            for e in cat_groups.get(cat_id, []):
                old_to_new[e["num"]] = num
                e["num"] = num
                new_entries.append(e)
                num += 1

        self._entries = new_entries
        # URL→num 매핑도 갱신
        for key, old_num in list(self._url_to_num.items()):
            if old_num in old_to_new:
                self._url_to_num[key] = old_to_new[old_num]

    def get_num(self, url: str) -> int | None:
        return self._url_to_num.get(url)

    def all_entries(self) -> list[dict]:
        return self._entries


# ── 품질 검증 게이트 (GTM copywriting-core + newsletter-management) ──
_COVERAGE_GAPS: list[dict] = []  # 서술 없이 말미 부착된 기사 누적

def _patch_summary_with_facts(current_text: str, unmatched_articles: list[dict],
                               facts_lookup: dict[str, str] | None = None) -> str:
    """갭 기사들의 한국어 헤드라인 text를 current_text 끝에 한 문장씩 붙여 반환."""
    additions: list[str] = []
    for a in unmatched_articles:
        # 헤드라인 text 필드 = insight-synthesizer가 이미 한국어로 작성한 요약
        hl_text = a.get("text", "").strip()
        if not hl_text:
            continue
        # 마침표로 끝나지 않으면 추가
        sent = hl_text if hl_text.endswith(".") else hl_text + "."
        additions.append(sent)
    if additions:
        sep = " " if current_text and not current_text.endswith((" ", "\n")) else ""
        return current_text + sep + " ".join(additions)
    return current_text

# ── 톤/문체 규칙 (도메인팩 style.yaml 에서 구성) ──────────────
_STYLE_PACK = domainpack.load_pack("style")
_TONE_BLACKLIST = list(_STYLE_PACK.get("tone_blacklist", []))
_BANNED_ENDINGS = list(_STYLE_PACK.get("banned_endings", []))
_KOREAN_SENTENCE_MAX = _STYLE_PACK.get("sentence_max", 60)  # CLAUDE.md 규정

def _ko_len(sent: str) -> int:
    """문장 길이 — 영문·숫자·통화 토큰 제외 (회사명·제품명·금액이 한국어 만연체 판정을
    오염시키지 않게. 60자 규칙의 취지는 한국어 문장 구조 단속이다)."""
    import re as _re
    return len(_re.sub(r"[A-Za-z0-9€$%&.,·'\-]+", "", sent))

def validate_briefing_quality(data: dict) -> list[str]:
    """TL;DR + 인사이트 품질 검증. 경고 목록 반환."""
    warnings = []
    import re as _re

    # TL;DR 검증
    tldr = data.get("tldr", "")
    if tldr:
        # 단정 표현 감지
        _assertion_patterns = ["포함되지 않았다", "포함 안 됐다", "편입되지 않았다",
                               "호명되지 않았", "포함되지 않았", "미포함",
                               "확인됐고", "확인되었고", "작동하기 시작했다"]
        for ap in _assertion_patterns:
            if ap in tldr:
                warnings.append(f"TL;DR: 미확인 단정 의심 '{ap}' — '확인 대상/미확인' 표기 권장")
        # 유추 비약
        for ap in ["같은 압력권", "같은 방식", "같은 패턴", "적용 가능"]:
            if ap in tldr:
                warnings.append(f"TL;DR: 유추 비약 의심 '{ap}'")
        # TL;DR 는 완결 서술문 단락 — 60자 제한·"~했고" 연결어 병치 검사 비적용
        # (insight-synthesizer 스펙 "TL;DR 문체": 연결어로 잇는 완결 문장 허용, 60자 비적용).
        # 단 극단적 만연체는 잡는다.
        for s in _re.split(r'[.!?]\s*', tldr):
            if len(s) > 150 and not _re.search(r'[a-zA-Z]{10,}', s):
                warnings.append(f"TL;DR: {len(s)}자 문장 (>150자 만연체) '{s[:30]}...'")
        # 블랙리스트
        for bw in _TONE_BLACKLIST:
            if bw in tldr:
                warnings.append(f"TL;DR: 블랙리스트 '{bw}'")

    return warnings + _validate_insights(data.get("insights", []))


def _validate_insights(insights: list[dict]) -> list[str]:
    """인사이트 품질 검증."""
    warnings = []
    for i, ins in enumerate(insights, 1):
        title = ins.get("title", "")
        imp = ins.get("implication", "")
        obs = ins.get("observation", "")

        # 1. Feature Dump 감지 — 제목에 쉼표/and로 뉴스 2개 이상 나열
        if title.count(",") >= 2 or title.count("、") >= 2:
            warnings.append(f"INSIGHT {i}: Feature Dump 의심 — 제목에 쉼표 나열 '{title[:50]}'")

        # 2. 블랙리스트 단어
        for text_name, text in [("제목", title), ("함의", imp), ("관찰", obs)]:
            for bw in _TONE_BLACKLIST:
                if bw in text:
                    warnings.append(f"INSIGHT {i} {text_name}: 블랙리스트 '{bw}' 사용")

        # 3. 금지 문미
        for ending in _BANNED_ENDINGS:
            if imp.endswith(ending) or imp.endswith(ending + "."):
                warnings.append(f"INSIGHT {i} 함의: 금지 문미 '{ending}'")

        # 4. 한국어 문장 길이 (60자 초과)
        import re as _re
        for text_name, text in [("함의", imp), ("관찰", obs)]:
            sentences = _re.split(r'[.!?]\s*', text)
            for s in sentences:
                if _ko_len(s) > _KOREAN_SENTENCE_MAX:
                    warnings.append(f"INSIGHT {i} {text_name}: 한글 {_ko_len(s)}자 문장 (>{_KOREAN_SENTENCE_MAX}자) '{s[:30]}...'")

        # 4.4 병치 비약 감지 (관찰) — 무관한 두 사건을 "동시에"로 한 문장에 엮기
        if "동시에" in obs:
            warnings.append(
                f"INSIGHT {i} 관찰: 병치 비약 의심 '동시에' — 무관한 사건이면 별도 문장으로 분리")

        # 4.5 제목 앞절 스텁 감지 — 대시 앞이 짧은 뉴스 라벨이면 통찰이 뒤에 있는 것
        if "—" in title or "–" in title:
            _head = _re.split(r"\s*[—–]\s*", title, maxsplit=1)[0].strip()
            if len(_head) < 12:
                warnings.append(
                    f"INSIGHT {i} 제목: 대시 앞이 스텁 '{_head}' — 통찰 문장이 대시 앞에 와야 함")

        # 5. 동향 라벨 제목 감지 — "~경쟁", "~추세", "~흐름" 등으로 끝나는 제목
        _trend_label_endings = ["경쟁", "추세", "흐름", "확산", "심화", "가속", "부상", "대두"]
        title_front = title.split("—")[0].strip() if "—" in title else title
        for tl in _trend_label_endings:
            if title_front.endswith(tl):
                warnings.append(f"INSIGHT {i} 제목: 동향 라벨 '{tl}' — 통찰이 아님, 거시적 변화 의미를 써야 함")

        # 6. 할일형 함의 감지 — "~확인 필요" 단독으로 끝나면 인사이트 아님
        _todo_patterns = ["확인 필요", "검토 필요", "대비 필요", "주시 필요", "모니터링 필요"]
        for tp in _todo_patterns:
            if title.endswith(tp):
                warnings.append(f"INSIGHT {i} 제목: 할일형 함의 '{tp}' — 통찰을 먼저, 할일은 뒤에")

        # 7. 회사명 나열 감지 — 제목에 · 구분자로 3개 이상 나열
        if title.count("·") >= 2:
            warnings.append(f"INSIGHT {i} 제목: 회사명 나열(·) — Feature Dump 변형 '{title[:50]}'")

        # 6. 유추 비약 감지 — "같은 방식", "같은 패턴", "적용 가능" 등
        _analogy_patterns = ["같은 방식", "같은 패턴", "같은 구조", "적용 가능한 경로",
                             "같은 역할", "동일한 패턴", "자사에도 적용"]
        for ap in _analogy_patterns:
            if ap in imp:
                warnings.append(f"INSIGHT {i} 함의: 유추 비약 의심 '{ap}' — 상황(시장/지역/규모) 동일 여부 확인 필요")

    return warnings


# ── HTML 유틸 ─────────────────────────────────────────────────
def esc(s: str) -> str:
    if not s:
        return ""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))

def domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return url


# ── src chip (CSS class 방식) ─────────────────────────────────
def src_chip(url: str, name: str, date: str, reg: SourceRegistry) -> str:
    num = reg.get_num(url)
    if not num:
        return ""
    short_date = date[5:] if date else ""
    label = f"[{num}] {name} {short_date}".strip()
    return f'<a class="src-chip" href="{esc(url)}">{esc(label)}</a>'


# ── 팩트 필드 정규화 (구/신 스키마 호환) ─────────────────────
def normalize_fact(f: dict) -> dict:
    return {
        "url":      f.get("source_url") or f.get("url", ""),
        "name":     f.get("source_name") or f.get("source", ""),
        "date":     f.get("source_date") or f.get("date", ""),
        "text":     f.get("text") or f.get("summary", ""),
        "title":    f.get("source_title", ""),
        "category": f.get("category") or f.get("from_category", "기타"),
        "summary":  f.get("fact_summary", ""),
    }


# ── 섹션 렌더러 ───────────────────────────────────────────────
def render_tldr(tldr: str) -> str:
    paragraphs = [p.strip() for p in tldr.split("\n\n") if p.strip()]

    # LLM이 빈 줄 없이 한 덩어리로 쓴 경우 결정론 폴백:
    # "자사~"로 시작하는 문장(자사 함의)부터 새 문단 — 외부 팩트 / 자사 함의 구조.
    if len(paragraphs) == 1:
        import re as _re
        sents = [s for s in _re.split(r'(?<=[.!?])\s+', paragraphs[0]) if s.strip()]
        for idx, s in enumerate(sents):
            if idx > 0 and s.startswith("자사"):
                paragraphs = [" ".join(sents[:idx]), " ".join(sents[idx:])]
                break

    if not paragraphs:
        paragraphs = [tldr]
    # 문단 간격은 빈 줄(<br><br>)보다 좁게 — 7px 마진
    content = "".join(
        f'<div class="tldr-p" style="margin-bottom:{"10px" if i < len(paragraphs)-1 else "0"};">{esc(p)}</div>\n  '
        for i, p in enumerate(paragraphs)
    ).strip()
    return (
        '<div style="background:#fff;border:1px solid #e7e5e4;border-radius:8px;'
        'padding:20px 24px;margin-bottom:32px;font-size:15px;color:#292524;line-height:1.65;">\n'
        f'  {content}\n'
        '</div>\n'
    )


def render_company_glossary(glossary: list[dict]) -> str:
    """이번 호 등장 기업 — 낯선 회사 1줄 설명. 참고용 부록(출처 앞) 컴팩트 2단 표.

    데이터: [{"name": "NEURA Robotics", "desc": "독일 휴머노이드 스타트업"}, ...]
    insight-synthesizer 가 비주류 기업만 골라 생성. 없으면 렌더하지 않음.
    """
    if not glossary:
        return ""
    # 대소문자 무관 중복 제거 (Theker/THEKER 등)
    seen: set = set()
    deduped = []
    for g in glossary:
        key = g.get("name", "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(g)
    glossary = deduped
    # 국가별 그룹핑 — desc가 "[국가] ..."로 시작하므로 첫 단어로 정렬(stable)
    glossary = sorted(glossary,
                      key=lambda g: (g.get("desc", "") or g.get("description", "")).split(" ")[0])
    items = ""
    for g in glossary:
        name = esc(g.get("name", ""))
        desc = esc(g.get("desc", "") or g.get("description", ""))
        if not name or not desc:
            continue
        items += (
            '  <div style="break-inside:avoid;padding:3px 0;">'
            f'<span style="font-weight:700;color:#57534e;">{name}</span><br>'
            f'<span style="color:#a8a29e;">{desc}</span></div>\n'
        )
    if not items:
        return ""
    return (
        '<h2>이번 호 등장 기업 <span style="font-size:13px;font-weight:400;'
        'color:#a8a29e;">참고용</span></h2>\n'
        '<div style="column-count:2;column-gap:24px;font-size:12px;'
        'line-height:1.6;">\n'
        f'{items}'
        '</div>\n'
    )


def swap_insight_title(title: str) -> str:
    """'근거 — 결론' → '결론 — 근거' (결론 선두)."""
    sep = " — "
    if sep in title:
        before, after = title.split(sep, 1)
        return f"{after}{sep}{before}"
    return title


def insight_fact_title(raw_title: str) -> str:
    """인사이트 제목에서 '—' 앞 통찰 문장만 남긴다 (뒤 자사 함의는 함의 블록과 중복).

    단, 앞절이 스텁("NEURA $1.4B" 같은 뉴스 라벨, 12자 미만)이면 LLM이
    통찰을 대시 뒤에 쓴 것 — 자르면 라벨만 남으므로 전체 제목을 그대로 노출.
    em/en 대시 모두 처리.
    """
    import re as _re
    head = _re.split(r"\s*[—–]\s*", raw_title, maxsplit=1)[0].strip()
    if len(head) < 12:
        return raw_title.strip()
    return head


def render_insight_card(insight: dict, num: int, reg: SourceRegistry) -> str:
    title        = esc(insight_fact_title(insight.get("title", "")))
    observation  = esc(insight.get("observation", ""))
    implication  = esc(insight.get("implication", ""))
    self_context = esc(insight.get("self_context") or insight.get("self_context_crossing", ""))
    raw_facts    = insight.get("facts") or insight.get("supporting_facts", [])

    # 관찰 텍스트에 인라인 출처 링크 — 내용 매칭(attach_inline_refs).
    # 문장 순서 기계 배정은 fact 순서와 서술 순서가 어긋나면 엉뚱한 문장에
    # 번호가 붙음 (BD 문장에 LG 기사 번호가 가던 버그).
    fact_articles = []
    for raw in raw_facts:
        f = normalize_fact(raw)
        if _is_self_mention(f.get("title", ""), f["text"]):
            continue
        if f["url"]:
            # 토큰 매칭용 제목: 한국어 bullet(text)이 관찰 문장과 어휘가 겹침
            fact_articles.append({"url": f["url"], "title": f["text"] or f["title"]})

    obs_with_links = attach_inline_refs(observation, fact_articles, reg)

    num_roman = ["①","②","③","④","⑤"][num - 1] if num <= 5 else str(num)

    # self_context 가 있으면 implication 에 통합
    implication_combined = implication
    if self_context:
        implication_combined = f"{self_context}<br><br>{implication}" if implication else self_context

    return (
        f'<div class="insight">\n'
        f'  <div class="insight-hd">'
        f'<span class="insight-num">INSIGHT {num_roman}</span> {title}</div>\n'
        f'  <div class="insight-body">\n'
        f'    <div class="insight-sec">\n'
        f'      <div class="sec-lbl lbl-obs">관찰</div>\n'
        f'      <p>{obs_with_links.strip()}</p>\n'
        f'    </div>\n'
        f'    <div class="insight-sec">\n'
        f'      <div class="sec-lbl lbl-imp">자사 함의</div>\n'
        f'      <p>{implication_combined}</p>\n'
        f'    </div>\n'
        f'  </div>\n'
        f'</div>\n'
    )


def attach_inline_refs(escaped_text: str, articles: list[dict],
                       reg: "SourceRegistry",
                       extra_candidates: list[dict] | None = None,
                       warn_label: str = "",
                       dump_unmatched: bool = True) -> str:
    """서술 문장에 기사 출처번호 [n]을 내용 매칭으로 붙인다.

    문장 순서가 아니라 기사 제목 토큰(회사명·고유명사)이 등장하는 문장에 붙인다 —
    위치 기반 배정은 서술 순서와 기사 순서가 어긋나면 엉뚱한 문장에 번호가 간다.
    articles 중 어느 문장에도 안 걸린 기사는 말미에 모아 붙여 누락을 막는다.
    extra_candidates(예: 경쟁사 그룹 헤드라인)는 매칭될 때만 붙이고 미매칭은 버린다 —
    카테고리 서술이 경쟁사 기사를 인용할 때 번호 누락을 막되, 무관 기사 강제 부착은 방지.
    [n] 스타일은 인사이트 관찰과 동일(같은 줄 높이, 위첨자 아님).
    escaped_text 는 이미 esc 처리된 문자열. articles: [{url|source_url, title|text, ...}].
    """
    import re as _re

    def _ref(a: dict) -> str:
        url = a.get("url", "") or a.get("source_url", "")
        num = reg.get_num(url)
        if not num:
            return ""
        return (f' <a href="{esc(url)}" style="color:#a8a29e;font-size:12px;'
                f'text-decoration:none;">[{num}]</a>')

    def _tokens(a: dict) -> list[str]:
        """기사 제목에서 매칭용 토큰 — 영문 3자+ / 한글 2자+ 고유명사 후보.

        범용 비즈니스 단어는 제외 — "투자" 하나로 무관 기사(ACS 유치 보도)가
        삼성 투자 문장에 붙는 오매칭 방지. 회사명·제품명이 신뢰할 앵커.
        """
        title = a.get("title", "") or a.get("text", "")
        toks = _re.findall(r"[A-Za-z][A-Za-z0-9.&-]{2,}|[가-힣]{2,}", title)
        stop = {"the", "and", "for", "with", "from", "into", "발표", "공개", "출시",
                "체결", "확대", "추진", "계획", "관련", "기사", "보도", "기업", "회사",
                "로봇", "robotics", "robot", "robots",
                "투자", "유치", "인수", "합병", "상장", "단독", "스타트업", "산업용",
                "시스템", "솔루션", "국내", "미국", "한국", "글로벌", "세계", "최초", "최대"}
        return [t for t in toks if t.lower() not in stop]

    if not escaped_text:
        return ""
    # 문단(빈 줄) 보존 — LLM이 주제 전환에 쓴 개행을 한 덩어리로 뭉개지 않는다
    paragraphs = [p.strip() for p in _re.split(r'\n\s*\n', escaped_text) if p.strip()]
    sentences: list[str] = []
    para_of: list[int] = []  # 각 문장이 속한 문단 인덱스
    for pi, para in enumerate(paragraphs):
        ps = [s for s in _re.split(r'(?<=[.!?。])\s+', para) if s.strip()]
        if not ps:
            ps = [para]
        sentences.extend(ps)
        para_of.extend([pi] * len(ps))
    if not sentences:
        sentences = [escaped_text]
        para_of = [0]

    # 기사 → 문장 배정: 제목 토큰이 가장 많이 겹치는 문장 (없으면 미배정)
    refs_per_sentence: dict[int, list[dict]] = {}
    unmatched: list[dict] = []
    seen_urls: set[str] = set()

    def _assign(a: dict, keep_unmatched: bool, min_score: int = 1):
        url = a.get("url", "") or a.get("source_url", "")
        if url and url in seen_urls:
            return
        toks = _tokens(a)
        best_si, best_score = -1, 0
        for si, sent in enumerate(sentences):
            sent_l = sent.lower()
            score = sum(1 for t in toks if t.lower() in sent_l)
            if score > best_score:
                best_si, best_score = si, score
        if best_si >= 0 and best_score >= min_score:
            refs_per_sentence.setdefault(best_si, []).append(a)
            if url:
                seen_urls.add(url)
        elif keep_unmatched:
            unmatched.append(a)
            if url:
                seen_urls.add(url)

    for a in articles:
        _assign(a, keep_unmatched=True)
    # 경쟁사 후보는 토큰 2개 이상 겹칠 때만 — 일반 단어("공동")·브랜드명 1개로
    # 무관 기사가 붙는 것 방지
    for a in (extra_candidates or []):
        _assign(a, keep_unmatched=False, min_score=2)

    # 문단 구분: 빈 줄(<br><br>)은 간격이 너무 뜸 — 호출부가 <p>로 감싸므로
    # 문단을 닫고 좁은 마진(6px)의 새 <p>로 잇는다
    _PARA_SEP = '</p><p style="margin:16px 0 0;">'
    out = ""
    for si, sent in enumerate(sentences):
        if si > 0:
            out += _PARA_SEP if para_of[si] != para_of[si - 1] else " "
        out += sent
        for a in refs_per_sentence.get(si, []):
            out += _ref(a)
    out = out.strip()
    if unmatched:
        # 이미 본문에 언급된 기사 제거 (키워드 2개 이상 겹치면 중복)
        out_lower = out.lower()
        truly_new = []
        for a in unmatched:
            toks = _tokens(a)
            if sum(1 for t in toks if t.lower() in out_lower) >= 2:
                out += _ref(a)  # 본문에 이미 언급 → ref만 붙임
            else:
                truly_new.append(a)

        # dump_unmatched=False: 미매칭 기사를 "이 밖에 ~ 등" 산문으로 욱여넣지 않는다.
        if not dump_unmatched:
            truly_new = []

        if truly_new:
            # 건수 무관하게 "이 밖에 ~ 등도 주목됐다." 형식으로 통일
            snippets = []
            for a in truly_new:
                hl = a.get("text", "").strip()
                short = _re.split(r"[,，。\.\(（—–\-]", hl)[0].strip()
                if short:
                    snippets.append(esc(short))
            refs_str = "".join(_ref(a) for a in truly_new)
            if snippets:
                joined = ", ".join(snippets)
                out += f" 이 밖에 {joined} 등도 주목됐다.{refs_str}"

        if warn_label and truly_new:
            for a in truly_new:
                t = (a.get("text", "") or a.get("title", ""))[:40]
                print(f"ℹ️  {warn_label}: 헤드라인 자동 보완 — '{t}'", flush=True)
    return out


def headline_inner_html(h: dict, reg: "SourceRegistry | None" = None) -> str:
    """헤드라인 항목의 본문 HTML(텍스트 + 언어태그 + 출처링크). li/div 래퍼 없음.

    헤드라인의 `/매체명` 이 이미 그 기사로 링크되므로 출처번호 [n] 은 붙이지 않는다
    (중복·시각 노이즈). 인라인 [n] 번호는 인사이트·카테고리 산문에서만 쓴다.
    reg 인자는 호출부 호환을 위해 남겨두되 사용하지 않는다.
    """
    url = h.get("url", "") or h.get("source_url", "")
    raw_sname = resolve_media_name(h.get("source", ""))
    sname = esc(raw_sname)
    lang_tag = ('' if _is_korean_media(raw_sname, url)
                else ' <span style="color:#c4b5a4;font-size:11px;">(영문)</span>')
    if url and sname:
        src = (f' <a href="{esc(url)}" style="color:#a8a29e;font-size:12px;'
               f'text-decoration:none;">/{sname}</a>')
    elif sname:
        src = f' <span style="color:#a8a29e;font-size:12px;">/{sname}</span>'
    else:
        src = ""
    return f'{esc(h.get("text", ""))}{lang_tag}{src}'


def render_category_summary_blocks(category_summary: list[dict],
                                   reg: SourceRegistry,
                                   headlines_by_group: dict[str, list] | None = None,
                                   facts_lookup: dict[str, str] | None = None) -> str:
    """카테고리별 동향 — 서술 요약에 기사 출처번호를 인라인 [n]으로 붙인다.

    별도 기사 나열·소스칩 없이 서술 안에서 모든 관련 기사를 번호로 링크(빠짐 방지).
    그 카테고리 헤드라인 기사 전체를 번호 대상으로 사용한다(헤드라인 섹션은 경쟁사 전용).
    """
    headlines_by_group = headlines_by_group or {}
    # LLM 출력 순서는 실행마다 달라짐 — 카테고리 정식 순서(CAT_ORDER)로 고정.
    # 미정의 카테고리는 뒤에 원래 순서대로.
    def _cat_rank(c):
        cid = c.get("category_id", "")
        return CAT_ORDER.index(cid) if cid in CAT_ORDER else len(CAT_ORDER)
    category_summary = sorted(category_summary, key=_cat_rank)
    html = '<h2>카테고리별 동향</h2>\n'
    for cat in category_summary:
        cat_id  = cat.get("category_id", "")
        color   = CAT_COLORS.get(cat_id, "#78716c")  # JSON 색상 무시, CAT_COLORS 강제
        name    = esc(cat.get("category_name", ""))
        text    = esc(cat.get("summary", ""))
        dot_cls = CAT_DOT_CLASSES.get(cat_id, "")
        dot     = (f'<div class="cat-dot {dot_cls}"></div>'
                   if dot_cls
                   else f'<div class="cat-dot" style="background:{color};"></div>')

        # 이 카테고리 기사 = 합성기 헤드라인(한국어 text) + reg 백필(나머지 수집분).
        # 수집된 그 카테고리 전 기사를 대상으로 삼아, 중요한 건 산문에 인라인 [n],
        # 나머지는 dump_unmatched 가 "이 밖에 ~ 등 [n]" 으로 쓸어담는다. 자사 언급 제외.
        selected = headlines_by_group.get(cat_id) or cat.get("sources", [])
        seen_u = {(a.get("url", "") or a.get("source_url", "")) for a in selected}
        arts = list(selected)
        for e in reg.all_entries():
            if e.get("category") != cat_id:
                continue
            u = e.get("url", "")
            if u and u in seen_u:
                continue
            seen_u.add(u)
            arts.append({"url": u, "title": e.get("title", ""), "text": e.get("title", "")})
        arts = [a for a in arts
                if not _is_self_mention(a.get("title", "") or a.get("text", ""),
                                        a.get("summary", ""))]

        # 경쟁사 그룹 + 타 카테고리 헤드라인 — 카테고리 서술이 다른 그룹 기사를
        # 인용할 때 번호 누락 방지. 매칭될 때만 부착(미매칭은 버림).
        extra = [h for g, items in headlines_by_group.items()
                 if g != cat_id for h in items
                 if not _is_self_mention(h.get("title", "") or h.get("text", ""),
                                         h.get("summary", ""))]

        cat_label = f"카테고리[{cat.get('category_name', cat_id)}]"
        # dump_unmatched=True: 산문에 안 엮인 수집 기사를 "이 밖에 ~ 등도 주목됐다 [n]"
        # 으로 쓸어담아, 그 카테고리의 모든 출처번호가 본문에 1회 이상 등장하게 한다.
        body = attach_inline_refs(text, arts, reg, extra_candidates=extra,
                                  warn_label=cat_label,
                                  dump_unmatched=True) if text else ""

        html += (
            f'<div class="summary-block" style="border-left-color:{color};">\n'
            f'  <div class="sb-hd">{dot}{name}</div>\n'
        )
        if body:
            html += f'  <p>{body}</p>\n'
        html += '</div>\n'
    return html


def render_sources(reg: SourceRegistry, total_articles: int = 0) -> str:
    entries = reg.all_entries()
    if not entries:
        return ""

    # (label, color) 튜플 — 도메인팩 categories.yaml 에서 일반적으로 구성.
    # 카테고리 id 를 하드코딩하지 않는다(도메인팩마다 다름).
    CAT_NAMES = {
        cid: (_CAT_DEFS[cid]["label_ko"], _CAT_DEFS[cid]["color"])
        for cid in _CAT_PACK.get("order", [])
        if cid in _CAT_DEFS
    }
    for _fid, _fdef in _CAT_FALLBACKS.items():
        if isinstance(_fdef, dict) and _fdef.get("label_ko") and _fdef.get("color"):
            CAT_NAMES.setdefault(_fid, (_fdef["label_ko"], _fdef["color"]))
    # "기타" 별칭 → default(없으면 중립)
    _dflt = _CAT_FALLBACKS.get("default") or {}
    CAT_NAMES.setdefault("기타", (_dflt.get("label_ko", "기타"), _dflt.get("color", "#9ca3af")))

    cat_groups: dict[str, list] = {}
    for e in entries:
        cat_groups.setdefault(e.get("category", "기타"), []).append(e)

    order = CAT_ORDER + [k for k in cat_groups if k not in CAT_ORDER]

    shown = total_articles if total_articles else len(entries)
    # 헤드라인에서 이미 전체 기사 나열 → 출처 목록은 컴팩트 번호+링크만
    html = (
        f'<h2>출처 <span style="font-size:14px;font-weight:400;color:#a8a29e;">'
        f'({len(entries)}건)</span></h2>\n'
        f'<div style="column-count:2;column-gap:24px;font-size:12px;color:#78716c;line-height:1.8;">\n'
    )

    for e in entries:
        num  = e["num"]
        url  = e["url"]
        name = e["name"]
        if url:
            dom = esc(domain_from_url(url))
            html += f'  <div style="padding:2px 0;"><span style="color:#a8a29e;font-weight:700;">[{num}]</span> <a href="{esc(url)}" style="color:#57534e;text-decoration:none;">{dom}</a></div>\n'
        else:
            html += f'  <div style="padding:2px 0;"><span style="color:#a8a29e;font-weight:700;">[{num}]</span> {esc(name)}</div>\n'

    html += '</div>\n'
    return html


def _render_sources_legacy(entries: list[dict], total_articles: int,
                           reg: "SourceRegistry") -> str:
    """Full source list with descriptions — kept for reference, not used in default render."""
    # (label, color) 튜플 — 도메인팩 categories.yaml 에서 일반적으로 구성(카테고리 id 하드코딩 X).
    CAT_NAMES = {
        cid: (_CAT_DEFS[cid]["label_ko"], _CAT_DEFS[cid]["color"])
        for cid in _CAT_PACK.get("order", [])
        if cid in _CAT_DEFS
    }
    for _fid, _fdef in _CAT_FALLBACKS.items():
        if isinstance(_fdef, dict) and _fdef.get("label_ko") and _fdef.get("color"):
            CAT_NAMES.setdefault(_fid, (_fdef["label_ko"], _fdef["color"]))
    _dflt = _CAT_FALLBACKS.get("default") or {}
    CAT_NAMES.setdefault("기타", (_dflt.get("label_ko", "기타"), _dflt.get("color", "#9ca3af")))

    cat_groups: dict[str, list] = {}
    for e in entries:
        cat_groups.setdefault(e.get("category", "기타"), []).append(e)

    order = CAT_ORDER + [k for k in cat_groups if k not in CAT_ORDER]

    shown = total_articles if total_articles else len(entries)
    html = (
        f'<h2>기사 출처 목록 <span style="font-size:14px;font-weight:400;color:#a8a29e;">'
        f'({len(entries)}건)</span></h2>\n'
    )

    for cat_id in order:
        if cat_id not in cat_groups:
            continue
        label, color = CAT_NAMES.get(cat_id, (cat_id, "#78716c"))
        html += (
            f'<div style="margin-bottom:10px;font-size:12px;font-weight:700;color:#57534e;'
            f'display:flex;align-items:center;padding:6px 0;'
            f'border-bottom:1px solid #e7e5e4;">'
            f'<div style="width:10px;height:10px;border-radius:50%;'
            f'background:{color};flex-shrink:0;margin-right:10px;"></div>{esc(label)}</div>\n'
            f'<div style="margin-bottom:16px;">\n'
        )
        for e in cat_groups[cat_id]:
            num   = e["num"]
            url   = e["url"]
            name  = e["name"]
            date  = e["date"]
            summ  = e.get("summary", "")
            title = e.get("title", "")

            badge = (
                f'<span style="font-size:11px;font-weight:700;color:#a8a29e;background:#f5f5f4;'
                f'border-radius:50%;width:20px;height:20px;display:inline-flex;'
                f'align-items:center;justify-content:center;flex-shrink:0;margin-right:8px;">{num}</span>'
            )

            if url:
                dom  = esc(domain_from_url(url))
                link = f'<a href="{esc(url)}" style="color:#2563eb;font-weight:600;text-decoration:none;">{dom}</a>'
            else:
                link = f'<span style="color:#78716c;font-weight:600;">{esc(name)}</span>'

            after = f' — {esc(title)}' if title else (f' — {esc(name)}' if url else '')
            summ_html = (
                f'<br><span style="color:#78716c;font-size:12px;">{esc(summ)}</span>'
                if summ else ''
            )

            html += (
                f'  <div style="font-size:13px;padding:11px 0;border-bottom:1px solid #f5f5f4;'
                f'display:flex;align-items:baseline;">\n'
                f'    {badge}\n'
                f'    <div>{link}{after} '
                f'<span style="color:#a8a29e;font-size:12px;">({esc(date)})</span>'
                f'{summ_html}</div>\n'
                f'  </div>\n'
            )
        html += '</div>\n'
    return html


# ── 메인 빌드 ─────────────────────────────────────────────────
def build_html(data: dict, date_str: str,
               collection_start: str = "",
               foreign_count: int = 0,
               domestic_count: int = 0,
               hours: int = 24,
               facts_sources: list[dict] | None = None) -> str:

    load_self_blocklist()
    load_media_mapping()
    load_competitor_aliases()
    reg = SourceRegistry()

    # 품질 검증 게이트 (GTM copywriting-core)
    quality_warnings = validate_briefing_quality(data)
    if quality_warnings:
        print("⚠️  품질 경고:")
        for w in quality_warnings:
            print(f"   {w}")
        print()

    # 소스 레지스트리 구축 — insights 먼저 (문서 상단부터 순번 부여)
    for insight in data.get("insights", []):
        for raw in insight.get("facts") or insight.get("supporting_facts", []):
            f = normalize_fact(raw)
            reg.register(url=f["url"], name=f["name"], date=f["date"],
                         summary=f["summary"] or f["text"],
                         category=f["category"], title=f["title"])

    # category_summary sources — insights 이후, all_sources 이전
    cat_summary_map = {c.get("category_id", "기타"): c
                       for c in data.get("category_summary", [])}
    reg_order = CAT_ORDER + [k for k in cat_summary_map if k not in CAT_ORDER]
    for cat_id in reg_order:
        cat = cat_summary_map.get(cat_id)
        if cat:
            for src in cat.get("sources", []):
                reg.register(
                    url=src.get("url", ""), name=src.get("name", ""),
                    date=src.get("date", ""), summary=src.get("summary", ""),
                    category=cat_id, title=src.get("title", ""),
                )

    # all_sources — 나머지 미등록 기사
    for src in data.get("all_sources", []):
        reg.register(
            url=src.get("url", ""), name=src.get("name", src.get("source_name", "")),
            date=src.get("date", src.get("source_date", "")),
            summary=src.get("summary", ""),
            category=src.get("category", src.get("from_category", "기타")),
            title=src.get("title", src.get("source_title", "")),
        )

    # headlines — 카테고리 동향 인라인 번호용. all_sources 누락분 보강(url dedup).
    for h in data.get("headlines", []):
        grp = h.get("group", "")
        reg.register(
            url=h.get("url", "") or h.get("source_url", ""),
            name=resolve_media_name(h.get("source", "")),
            date=h.get("date", ""),
            summary="",
            category=grp if grp in CAT_ORDER else "기타",
            title=h.get("text", ""),
        )

    # facts(newsletter-facts JSON) — 결정론 백필. LLM이 all_sources에서 기사를
    # 빠뜨려도 수집·집계된 전체 기사가 출처 목록에 반드시 들어가게 보장.
    for f in (facts_sources or []):
        reg.register(
            url=f.get("source_url", ""),
            name=f.get("source_name", ""),
            date=f.get("source_date", ""),
            summary=f.get("summary", ""),
            category=f.get("_category", "기타"),
            title=f.get("title", ""),
        )

    # 전체 등록 완료 후, 카테고리별 순서로 재넘버링 (출처 목록과 칩 번호 일치)
    reg.renumber_by_category(CAT_ORDER)

    # 실제 등록된 소스 수 (블로그/중복 제거 후) 기준으로 국내/해외 재계산
    # CLI로 받은 foreign/domestic 은 run-post.sh 가 all_sources 전체 기준으로 계산한 값이라
    # reg 필터(블로그·중복 제거)를 거친 실제 렌더 건수와 1건 이상 어긋날 수 있음 → reg 기준으로 재계산
    def _is_domestic_entry(e: dict) -> bool:
        url  = e.get("url", "")
        name = e.get("name", "")  # \ub9e4\uccb4\uba85\ub9cc \uae30\uc900 \u2014 title\uc740 \ud55c\uae00 \ubc88\uc5ed \uc81c\ubaa9\uc774 \uc11e\uc5ec \uc624\ud310\ud568
        if ".kr/" in url or url.rstrip("/").endswith(".kr"):
            return True
        if any("\uac00" <= c <= "\ud7a3" for c in name):
            return True
        return False

    all_entries = reg.all_entries()
    total_articles = len(all_entries)
    actual_domestic = sum(1 for e in all_entries if _is_domestic_entry(e))
    actual_foreign  = total_articles - actual_domestic

    # 메타라인
    date_ko = format_date_ko(date_str)
    import math
    days = math.ceil(hours / 24) if hours > 0 else 0
    parts   = [date_ko]
    if days > 0:
        parts.append(f"최근 {days}일")
    if total_articles:
        if actual_foreign or actual_domestic:
            parts.append(f"{total_articles}건 (해외 {actual_foreign}, 국내 {actual_domestic})")
        else:
            parts.append(f"{total_articles}건")
    meta_line = " · ".join(parts)

    # footer 범위 문자열
    if collection_start:
        start_parts = collection_start.split("-")
        end_parts   = date_str.split("-")
        if start_parts[0] == end_parts[0]:
            range_str = f"{collection_start} ~ {end_parts[1]}-{end_parts[2]}"
        else:
            range_str = f"{collection_start} ~ {date_str}"
    else:
        range_str = date_str

    html = f"""<!DOCTYPE html>
<html lang="ko" bgcolor="white">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>산업군 인사이트 뉴스레터 — {date_str}</title>
<style>
{CSS}
</style>
</head>
<body bgcolor="white" style="background-color:#ffffff;">

<div class="brand">{esc(HTML_HEADER_NEWSLETTER)}</div>
<h1>산업군 인사이트 뉴스레터</h1>
<div class="meta-line">{meta_line}</div>

"""

    tldr = data.get("tldr", "")
    if tldr:
        html += render_tldr(tldr)

    # 오늘의 기사 — 헤드라인 섹션은 경쟁사 동향 전용. 카테고리 기사는
    # headlines_by_group 으로 모아 '카테고리별 동향' 블록에서 1회만 노출.
    headlines_by_group: dict[str, list] = {}
    _BLOG_FILTER = ["tistory.com", "blog.naver.com", "brunch.co.kr", "medium.com/@", "velog.io", "notion.so"]
    import re as _re_hl
    def _clean_headline_text(text: str, source_name: str = "") -> str:
        """text 끝에 붙은 /매체명 반복 제거 + 본문 내 source_name 중복 방어"""
        # 반복 strip — /한국경제TV /한국경제TV 같은 다중 발생 대응
        prev = None
        while prev != text:
            prev = text
            text = _re_hl.sub(r'\s*/[^\s/]{2,30}\s*$', '', text).strip()
        # source_name이 text 끝에 남아있으면 제거 (슬래시 없이 붙은 경우)
        if source_name:
            sn = source_name.strip()
            if text.endswith(sn):
                text = text[:-len(sn)].rstrip(' /').strip()
        return text
    headlines = [h for h in data.get("headlines", [])
                 if not any(bd in (h.get("url", "") or h.get("source_url", "")) for bd in _BLOG_FILTER)]
    for h in headlines:
        resolved = resolve_media_name(h.get("source", ""))
        h["text"] = _clean_headline_text(h.get("text", ""), resolved)
    if headlines:

        # url → 카테고리 (오배정 경쟁사 그룹 재배정용)
        url_to_cat = {}
        for s in data.get("all_sources", []):
            u = s.get("url", "")
            c = s.get("category", s.get("from_category", ""))
            if u and c:
                url_to_cat[u] = c

        # 그룹 수집: 경쟁사 그룹 먼저, 그 다음 카테고리 그룹
        groups: dict[str, list] = {}
        competitor_names: list[str] = []  # 경쟁사 이름 순서 보존
        category_groups: list[str] = []

        for h in headlines:
            g = h.get("group", "기타")
            # 경쟁사 그룹 검증 — 헤드라인 텍스트에 그 회사명이 없으면 오배정
            # (리드 비교 언급 등) → 기사 카테고리로 재배정
            if g not in CAT_ORDER and g != "기타":
                if not competitor_in_text(g, h.get("text", "")):
                    u = h.get("url", "") or h.get("source_url", "")
                    g = url_to_cat.get(u) if url_to_cat.get(u) in CAT_ORDER else "기타"
            groups.setdefault(g, []).append(h)
            # 경쟁사인지 카테고리인지 구분 (CAT_ORDER에 없으면 경쟁사)
            if g not in CAT_ORDER and g != "기타" and g not in competitor_names:
                competitor_names.append(g)
            elif g in CAT_ORDER and g not in category_groups:
                category_groups.append(g)

        # 경쟁사 동향 — 인사이트/카테고리별 동향과 동일한 h2 섹션 +
        # 경쟁사별 summary-block 카드 서식으로 통일
        if competitor_names:
            html += '<h2>경쟁사 동향</h2>\n'
            for comp in competitor_names:
                items = groups.get(comp, [])
                if not items:
                    continue
                html += (
                    '<div class="summary-block" style="border-left-color:#1c1917;">\n'
                    f'  <div class="sb-hd">{esc(comp)}</div>\n'
                )
                for h in items:
                    html += (f'  <div style="font-size:14px;padding:4px 0;color:#292524;">'
                             f'{headline_inner_html(h, reg)}</div>\n')
                html += '</div>\n'

        # 카테고리별 헤드라인은 '카테고리별 동향' 블록으로 이관(중복 제거).
        # groups 를 headlines_by_group 으로 노출해 render_category_summary_blocks 에 전달.
        headlines_by_group = groups

        # 기타 (매칭 안 된 것 — 경쟁사·카테고리 어디에도 안 들어간 기사)
        etc = groups.get("기타", [])
        if etc:
            html += '<div class="hl-group">\n  <div class="hl-group-hd">기타</div>\n  <ul>\n'
            for h in etc:
                html += f'    <li>{headline_inner_html(h, reg)}</li>\n'
            html += '  </ul>\n</div>\n'

    # 이번 호 등장 기업 — 경쟁사 동향 뒤, 인사이트 직전.
    # 낯선 이름이 본격 등장하는 인사이트·카테고리 동향 앞에 컴팩트하게 배치.
    html += render_company_glossary(data.get("company_glossary", []))

    insights = data.get("insights", [])
    if insights:
        html += '<h2>인사이트</h2>\n'
        for i, insight in enumerate(insights, 1):
            html += render_insight_card(insight, i, reg)

    # 커버리지 상태표
    # 커버리지 상태 — 헤드라인에서 이미 카테고리별 건수 확인 가능하므로 렌더링 생략

    cat_summary = data.get("category_summary", [])
    if cat_summary:
        # url → summary 맵: 갭 기사 보완용 (LLM 없이 facts에서 직접)
        _facts_lookup = {
            f.get("source_url", ""): f.get("summary", "") or f.get("title", "")
            for f in (facts_sources or []) if f.get("source_url")
        }
        html += render_category_summary_blocks(cat_summary, reg, headlines_by_group,
                                               facts_lookup=_facts_lookup)

    html += render_sources(reg, total_articles)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    footer_count = total_articles if total_articles else len(reg.all_entries())
    html += f"""
<div class="footer">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
    <div>
      <div>수집 기간: {range_str} &nbsp;·&nbsp; 출처 {footer_count}건</div>
      <div style="margin-top:3px;">생성 {generated} KST</div>
    </div>
    <div style="text-align:right;">
      {esc(HTML_FOOTER)}<br>
      <span style="color:#d1d5db;">모든 주장은 출처 URL 원문으로 검증 가능</span>
    </div>
  </div>
</div>

</body>
</html>"""

    return html


# ── CLI ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="briefing-formatter v3")
    parser.add_argument("--input",  required=True, help="insight-synthesizer JSON")
    parser.add_argument("--date",   required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", required=True, help="출력 HTML 경로")
    parser.add_argument("--collection-start", default="", help="수집 시작일 YYYY-MM-DD")
    parser.add_argument("--foreign",  type=int, default=0)
    parser.add_argument("--domestic", type=int, default=0)
    parser.add_argument("--hours",    type=int, default=24, help="수집 시간 범위 (24/72 등)")
    parser.add_argument("--facts",    default="",
                        help="newsletter-facts JSON 경로 — 출처 목록 결정론 백필 (LLM 누락 방지)")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))

    facts_sources: list[dict] = []
    if args.facts and Path(args.facts).exists():
        facts_data = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        for cat in facts_data.get("categories", []):
            cat_id = cat.get("category_id", "기타")
            for f in cat.get("facts", []):
                facts_sources.append({**f, "_category": cat_id if cat_id != "uncategorized" else "기타"})

    html = build_html(
        data, args.date,
        collection_start=args.collection_start,
        foreign_count=args.foreign,
        domestic_count=args.domestic,
        hours=args.hours,
        facts_sources=facts_sources,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    # 품질 경고를 사이드파일로 기록 — run-post.sh 발송 게이트가 읽는다 (CLAUDE.md §7)
    quality_warnings = validate_briefing_quality(data)
    qw_path = out.parent / f".quality-warnings-{args.date}.json"
    qw_path.write_text(
        json.dumps(quality_warnings, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {args.output}")
    print(f"   날짜: {args.date} · 수집기간: {args.collection_start}~{args.date}")

    if _COVERAGE_GAPS:
        remaining = [{"label": g["label"], "title": g["title"]} for g in _COVERAGE_GAPS]
        print(f"⚠️  보완 후 잔존 갭 {len(remaining)}건: {remaining}", flush=True)


if __name__ == "__main__":
    main()
