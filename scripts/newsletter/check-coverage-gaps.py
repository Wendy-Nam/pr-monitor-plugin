#!/usr/bin/env python3
"""briefing JSON에서 커버리지 누락 기사를 탐지한다.

커버리지 규약(신): 그 카테고리 전 기사(tier1+tier2)는
  - `headlines` 에 한국어로 포함되거나 (format.py 가 한 줄 나열로 렌더), 또는
  - `category_summary` 산문에서 서술되어야 한다 (tier1 주요소식).
둘 다 아니면 갭 — 해당 기사는 영문 원제목으로 폴백되거나 본문에서 사라진다.

format.py 실행 전에 호출해 갭을 미리 잡는다.
갭이 있으면 gaps JSON을 stdout에 출력하고 exit 1. 없으면 exit 0.

사용:
  python3 scripts/newsletter/check-coverage-gaps.py 2026-06-12
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"

CAT_IDS = {"humanoid", "cobots", "amr", "manufacturing_platform", "other_industrial", "funding"}


def _normalize(text: str) -> str:
    """비교용 정규화: 소문자, 공백·구두점 제거."""
    return re.sub(r"[\s\W]+", "", text.lower())


def _headline_words(text: str) -> set[str]:
    """헤드라인에서 의미 있는 키워드 추출 (3자 이상 한글 어절 or 4자 이상 영문 단어)."""
    words: set[str] = set()
    for w in re.findall(r"[가-힣]{3,}|[A-Za-z]{4,}", text):
        words.add(w.lower())
    return words


def _covered(headline_text: str, summary_text: str) -> bool:
    """헤드라인 핵심 키워드가 summary에 1개 이상 포함되면 covered."""
    if not summary_text:
        return False
    norm_summary = _normalize(summary_text)
    for w in _headline_words(headline_text):
        if _normalize(w) in norm_summary:
            return True
    return False


def main() -> int:
    date_str = sys.argv[1] if len(sys.argv) > 1 else __import__("datetime").date.today().isoformat()

    briefing_path = PROCESSED / f"newsletter-briefing-{date_str}.json"
    if not briefing_path.exists():
        print(f"ERROR: {briefing_path.name} 없음", file=sys.stderr)
        return 1

    briefing = json.loads(briefing_path.read_text(encoding="utf-8"))

    # category_summary를 카테고리 ID → 서술 맵으로 (스키마 필드는 summary, 구버전 text 호환)
    summary_map: dict[str, str] = {}
    for c in briefing.get("category_summary", []):
        cid = c.get("category_id", "")
        summary_map[cid] = c.get("summary", "") or c.get("text", "")

    # 검사 대상 = synthesis-context 의 카테고리 전체 (tier1 facts + tier2 헤드라인).
    # 카테고리 요약은 전부 산문이므로, 전 기사가 어떤 카테고리 요약에서든 서술돼야 한다.
    candidates: list[tuple[str, str]] = []  # (category_id, title)
    ctx_path = PROCESSED / f"synthesis-context-{date_str}.json"
    if ctx_path.exists():
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        for cat in ctx.get("categories", []):
            cid = cat.get("category_id", "")
            for f in cat.get("facts", []):
                candidates.append((cid, f.get("title", "")))
        for t2 in ctx.get("tier2_headlines", []):
            candidates.append((t2.get("category", ""), t2.get("title", "")))
    else:
        for h in briefing.get("headlines", []):
            candidates.append((h.get("group", "기타"), h.get("text", "")))

    all_summaries = " ".join(summary_map.values())
    gaps: list[dict] = []
    seen: set[str] = set()
    for group, text in candidates:
        if group not in CAT_IDS or not text:
            continue  # 경쟁사 그룹·미분류는 별도 섹션 — 서술 의무 없음
        key = _normalize(text)[:25]
        if key in seen:
            continue  # 같은 사건 복수 보도는 1건만 검사
        seen.add(key)
        summary = summary_map.get(group, "")
        if _covered(text, summary) or _covered(text, all_summaries):
            continue
        gaps.append({"category": group, "headline": text})
        print(f"⚠️  갭[{group}]: {text[:70]}", file=sys.stderr)

    if gaps:
        print(json.dumps(gaps, ensure_ascii=False))
        return 1

    print("✓ 커버리지 갭 없음", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
