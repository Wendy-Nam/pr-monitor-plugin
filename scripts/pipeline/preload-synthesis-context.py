#!/usr/bin/env python3
"""insight-synthesizer용 압축 컨텍스트 생성 (LLM 토큰 0원)

입력: data/processed/newsletter-facts-{date}.json
출력: data/processed/synthesis-context-{date}.json

로직:
1. tier1 기사만 추출 (tier2 제거)
2. 필수 필드만 유지 (title, summary, source_name, source_date, competitors_mentioned, relevance_score)
3. summary를 1문장으로 축약
4. 파일 크기를 ~200줄 이내로 유지

사용:
  python3 scripts/pipeline/preload-synthesis-context.py          # 오늘
  python3 scripts/pipeline/preload-synthesis-context.py 2026-06-12  # 지정일
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths

# raw/processed/cache live under PLUGIN_DATA; self-context lives under PROJECT_DIR.
FACTS_DIR = paths.PROCESSED_DIR
OUTPUT_DIR = paths.PROCESSED_DIR
SELF_CONTEXT_DIR = paths.SELF_CONTEXT_DIR

# 합성기에 인라인 번들할 자사 맥락 파일 (blind-spots.md 절대 포함 금지 — CLAUDE.md 3-3)
# competitor_landscape 는 YAML 에서 선택 발췌 (build_landscape_bundle)
SELF_CONTEXT_FILES = {
    "company_narrative": SELF_CONTEXT_DIR / "company-narrative.md",
    "key_events": SELF_CONTEXT_DIR / "key-events.yaml",
    "patterns_observed": SELF_CONTEXT_DIR / "patterns-observed.md",
}
LANDSCAPE_YAML = SELF_CONTEXT_DIR / "competitor-landscape.yaml"

# source_url 제외 — synthesizer는 URL을 복사하지 않고 id를 ref로 출력,
# resolve-refs.py가 렌더 전에 결정론적으로 조인 (토큰 절약 + 누락/중복 차단)
KEEP_FIELDS = {
    "id", "title", "summary", "source_name", "source_date",
    "competitors_mentioned", "relevance_score"
}


def _load_self_aliases() -> list[str]:
    """pr-queries.yaml self_aliases 로드 (소문자). 자사 기사 facts 유입 차단용."""
    candidates = [
        paths.PROJECT_DIR / "config" / "pr-queries.yaml",
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "pr-queries.yaml",
    ]
    for p in candidates:
        if p.exists():
            try:
                import yaml
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                return [str(a).lower() for a in data.get("self_aliases", []) if a]
            except Exception:
                pass
    return []


_SELF_ALIASES: list[str] = []


def _is_self_article(title: str, summary: str) -> bool:
    global _SELF_ALIASES
    if not _SELF_ALIASES:
        _SELF_ALIASES = _load_self_aliases()
    text = (title + " " + summary).lower()
    return any(a in text for a in _SELF_ALIASES)


def load_facts(date_str: str) -> dict:
    path = FACTS_DIR / f"newsletter-facts-{date_str}.json"
    if not path.exists():
        print(f"Error: {path} not found")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def truncate_summary(summary: str, max_sentences: int = 2) -> str:
    """첫 2문장만 유지."""
    sentences = re.split(r'(?<=[.!?])\s+', summary.strip())
    return ' '.join(sentences[:max_sentences])


def compress_article(article: dict) -> dict:
    compressed = {}
    for field in KEEP_FIELDS:
        if field in article:
            compressed[field] = article[field]
    compressed["summary"] = truncate_summary(compressed.get("summary", ""))
    return compressed


def compress_tier2_headline(article: dict, category_id: str) -> dict:
    """tier2 는 헤드라인 나열 전용 — 제목·ref·날짜·점수만. summary/본문 제외."""
    h = {
        "id": article.get("id", ""),
        "title": article.get("title", ""),
        "source_name": article.get("source_name", ""),
        "source_date": article.get("source_date", ""),
        "relevance_score": article.get("relevance_score"),
        "category": category_id,
    }
    comps = article.get("competitors_mentioned") or []
    if comps:
        h["competitors_mentioned"] = comps
    return h


def extract_tier1(facts: dict) -> dict:
    compressed_categories = []
    tier2_headlines = []
    total_tier1 = 0

    for cat in facts.get("categories", []):
        cat_id = cat["category_id"]
        tier1_articles = []
        for a in cat.get("facts", []):
            if _is_self_article(a.get("title", ""), a.get("summary", "")):
                continue  # 자사 기사는 newsletter facts 제외 (PR 클리핑 전담)
            if a.get("tier") == 1:
                tier1_articles.append(compress_article(a))
            else:
                tier2_headlines.append(compress_tier2_headline(a, cat_id))
        if tier1_articles:
            compressed_categories.append({
                "category_id": cat_id,
                "category_name": cat["category_name"],
                "tier1_count": len(tier1_articles),
                "facts": tier1_articles,
            })
            total_tier1 += len(tier1_articles)

    compressed_competitor = {}
    for comp, articles in facts.get("competitor_articles", {}).items():
        compressed_competitor[comp] = [
            compress_article(a) for a in articles
        ]

    return {
        "date": facts.get("date"),
        "total_tier1": total_tier1,
        "categories": compressed_categories,
        "competitor_articles": compressed_competitor,
        # tier2 인라인 — synthesizer가 newsletter-facts 전체를 Read 하지 않아도
        # 헤드라인 선별(최신성·경쟁사 그룹핑) 가능 (토큰 절감)
        "tier2_headlines": tier2_headlines,
    }


def collect_mentioned_competitors(context: dict) -> set[str]:
    """오늘 facts 에 언급된 경쟁사명 수집 (소문자)."""
    mentioned: set[str] = set()
    for cat in context.get("categories", []):
        for a in cat.get("facts", []):
            for c in a.get("competitors_mentioned", []) or []:
                mentioned.add(str(c).lower())
    for comp in context.get("competitor_articles", {}):
        mentioned.add(str(comp).lower())
    return mentioned


def build_landscape_bundle(mentioned: set[str]) -> str:
    """경쟁사 기준선 선택 발췌 — 오늘 언급된 경쟁사는 전체(baseline+updates),
    나머지는 baseline 1줄만. themes/self_implications 는 항상 포함."""
    import yaml
    if not LANDSCAPE_YAML.exists():
        return ""
    data = yaml.safe_load(LANDSCAPE_YAML.read_text(encoding="utf-8"))

    # alias → 키 매핑으로 mentioned 를 기준선 키로 해석
    keymap: dict[str, str] = {}
    for key, comp in data.get("competitors", {}).items():
        keymap[key.lower()] = key
        for a in (comp or {}).get("aliases", []) or []:
            keymap[a.lower()] = key
    hit_keys = {keymap[m] for m in mentioned if m in keymap}

    lines = [f"(기준선 스냅샷 — {data.get('meta', {}).get('note', '')})", ""]
    for key, comp in data.get("competitors", {}).items():
        comp = comp or {}
        base = (comp.get("baseline") or "").strip()
        if key in hit_keys:
            lines.append(f"- {key}: {base}" if base else f"- {key}:")
            for u in (comp.get("updates", []) or []) + (comp.get("archive", []) or []):
                src = f" ({u.get('source', '')}, {u.get('date', '')})"
                lines.append(f"  - [{u.get('dimension', '')}] {u.get('info', '')}{src}")
        elif base:
            lines.append(f"- {key}: {base}")
    if data.get("themes"):
        lines += ["", "[관통 주제]", data["themes"].strip()]
    if data.get("self_implications"):
        lines += ["", "[자사 대비 시사]", data["self_implications"].strip()]
    return "\n".join(lines)


def build_self_context_bundle(mentioned: set[str]) -> dict:
    """자사 맥락 파일을 인라인 번들 — synthesizer가 별도 Read 없이 사용."""
    bundle = {}
    for key, path in SELF_CONTEXT_FILES.items():
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                bundle[key] = text

    landscape = build_landscape_bundle(mentioned)
    if landscape:
        bundle["competitor_landscape"] = landscape

    # company-profile.yaml에서 competitors 블록만 (카테고리 정의 등 나머지는 불필요)
    profile_path = paths.CONFIG_DIR / "company-profile.yaml"
    if profile_path.exists():
        text = profile_path.read_text(encoding="utf-8")
        m = re.search(r"^competitors:.*?(?=^\w|\Z)", text, re.M | re.S)
        if m:
            bundle["competitors_yaml"] = m.group(0).strip()

    # 도메인팩 — 톤 규칙 + few-shot 판단 예시. 엔진이 프롬프트에 하드코딩하지 않고
    # 번들로 전달 → 새 조직은 자기 prompt-examples.yaml 만 채우면 합성기에 반영된다.
    from prmonitor import domainpack as dp
    for pack_name, bundle_key in (("style", "style_rules"),
                                  ("prompt-examples", "prompt_examples")):
        try:
            bundle[bundle_key] = dp.load_pack(pack_name)
        except dp.DomainPackError:
            pass
    return bundle


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else __import__("datetime").date.today().isoformat()

    facts = load_facts(date_str)
    context = extract_tier1(facts)
    context["self_context_bundle"] = build_self_context_bundle(
        collect_mentioned_competitors(context))

    out_path = OUTPUT_DIR / f"synthesis-context-{date_str}.json"
    out_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    line_count = len(out_path.read_text(encoding="utf-8").split("\n"))
    print(f"✓ {out_path}")
    print(f"  tier1: {context['total_tier1']}건")
    print(f"  tier2 헤드라인: {len(context['tier2_headlines'])}건 (제목만 인라인)")
    print(f"  categories: {len(context['categories'])}개")
    print(f"  lines: {line_count}")


if __name__ == "__main__":
    main()
