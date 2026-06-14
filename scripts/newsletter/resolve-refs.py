#!/usr/bin/env python3
"""briefing JSON의 ref id → 출처 메타데이터 결정론적 조인 (Step 8 전처리)

synthesizer는 기사 출처를 `"ref": "<id>"` 로만 출력한다 (URL 복사 금지).
이 스크립트가 newsletter-facts의 id 인덱스로 url/매체명/날짜/제목을 채운다.

- ref 있는 항목: id 조인 (1차)
- ref 없거나 미해결 + url 빈 항목: 제목 토큰 매칭 폴백 (2차)
- 둘 다 실패: 빈 url 유지 (format.py가 제목 키로 등록 — 링크 없는 출처)

사용:
  python3 scripts/newsletter/resolve-refs.py 2026-06-12
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths

# processed JSON은 PLUGIN_DATA의 data/processed에서 읽고 다시 쓴다
PROCESSED = paths.PROCESSED_DIR

# briefing 내 출처 필드명 ↔ newsletter-facts 필드명
FIELD_MAP = [
    # (briefing 키, facts 키)
    ("url", "source_url"),
    ("source_url", "source_url"),
    ("name", "source_name"),
    ("source_name", "source_name"),
    ("source", "source_name"),   # headline_inner_html: h.get("source", "")
    ("date", "source_date"),
    ("source_date", "source_date"),
    ("title", "title"),
    ("source_title", "title"),
]

STOP_TOKENS = {"the", "and", "for", "with", "from",
               "robot", "robots", "robotics", "humanoid", "ai"}


def en_tokens(s: str) -> set:
    toks = set(re.findall(r"[a-z][a-z0-9]{2,}|\$[\d.]+[bm]?", (s or "").lower()))
    return toks - STOP_TOKENS


def build_index(facts: dict) -> tuple[dict, list]:
    by_id: dict[str, dict] = {}
    articles: list[dict] = []
    for cat in facts.get("categories", []):
        for a in cat.get("facts", []):
            if a.get("id"):
                by_id[a["id"]] = a
            if a.get("source_url"):
                articles.append(a)
    for arts in facts.get("competitor_articles", {}).values():
        for a in arts:
            if a.get("id") and a["id"] not in by_id:
                by_id[a["id"]] = a
            if a.get("source_url"):
                articles.append(a)
    return by_id, articles


def fill_from(entry: dict, art: dict) -> None:
    """art의 메타데이터를 entry에 채운다 (기존 비어있지 않은 값은 보존).

    synthesizer가 {"ref","summary"}만 출력한 경우 키 자체가 없으므로
    표준 키(url/name/date/title)를 새로 추가한다. format.py normalize_fact가
    url↔source_url 등 양쪽 키를 모두 인식하므로 표준 키만 넣으면 충분.
    """
    for bk, fk in FIELD_MAP:
        if art.get(fk) and not entry.get(bk):
            entry[bk] = art[fk]


def title_fallback(entry: dict, articles: list) -> dict | None:
    query = entry.get("source_title") or entry.get("title") or entry.get("text", "")
    qt = en_tokens(query)
    if len(qt) < 2:
        return None
    sname = (entry.get("source_name") or entry.get("name", "")).lower()
    best, best_score = None, 0.0
    for a in articles:
        at = en_tokens(a.get("title", "") + " " + a.get("summary", ""))
        if not at:
            continue
        score = len(qt & at) / len(qt)
        if sname and sname in (a.get("source_name", "") or "").lower():
            score += 0.2
        if score > best_score:
            best, best_score = a, score
    return best if best_score >= 0.6 else None


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else __import__("datetime").date.today().isoformat()

    briefing_path = PROCESSED / f"newsletter-briefing-{date_str}.json"
    facts_path = PROCESSED / f"newsletter-facts-{date_str}.json"
    if not briefing_path.exists() or not facts_path.exists():
        print(f"ERROR: {briefing_path.name} 또는 {facts_path.name} 없음")
        sys.exit(1)

    briefing = json.loads(briefing_path.read_text(encoding="utf-8"))
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    by_id, articles = build_index(facts)

    stats = {"ref": 0, "fallback": 0, "unresolved": 0}

    def is_source_entry(o: dict) -> bool:
        return any(k in o for k in ("url", "source_url"))

    def walk(o):
        if isinstance(o, dict):
            if is_source_entry(o) or "ref" in o:
                art = by_id.get(o.get("ref", ""))
                if art:
                    fill_from(o, art)
                    stats["ref"] += 1
                elif not o.get("url") and not o.get("source_url"):
                    fb = title_fallback(o, articles)
                    if fb:
                        fill_from(o, fb)
                        stats["fallback"] += 1
                    else:
                        stats["unresolved"] += 1
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(briefing)
    briefing_path.write_text(
        json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✓ resolve-refs: id 조인 {stats['ref']}건, "
          f"제목 폴백 {stats['fallback']}건, 미해결 {stats['unresolved']}건")
    if stats["unresolved"] > 0:
        print("  (미해결 항목은 링크 없는 출처로 렌더됨)")

    # 미해결 비율 > 30% → exit 2 (호출부가 발송 보류 판단. briefing 저장은 완료된 상태)
    total = sum(stats.values())
    if total > 0 and stats["unresolved"] / total > 0.3:
        print(f"⚠️  미해결 비율 {stats['unresolved']}/{total} > 30% — 발송 보류 권고 (exit 2)")
        sys.exit(2)


if __name__ == "__main__":
    main()
