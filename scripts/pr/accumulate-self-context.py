#!/usr/bin/env python3
"""Step E: 자사 맥락 타임라인 결정론 축적 (LLM 호출 없음)

입력: data/output/pr/pr-monitoring-{date}.csv (gen-pr-monitor.py 산출 — Haiku 톤 포함)
대상: data/self-context/timeline/{YYYY-QN}.yaml

동작:
  - CSV 의 자사 언급 기사를 분기 타임라인에 append
  - 중복 제거: URL 동일 또는 (날짜, 제목) 동일이면 skip
  - 톤/언급유형/맥락은 PR 파이프라인이 이미 계산한 값을 그대로 보존
  - frame 등 해석 필드는 기록하지 않음 (월간 patterns-observed 가 담당)

사용:
  python3 scripts/pr/accumulate-self-context.py             # 오늘
  python3 scripts/pr/accumulate-self-context.py 2026-06-12  # 지정일
"""

from __future__ import annotations

import csv
import sys
from datetime import date as Date
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths

import yaml

# Plugin re-anchor: timeline lives under PROJECT_DIR self-context (paths.SELF_CONTEXT_DIR),
# replacing ref accumulate-self-context.py:27-28 (PROJECT_ROOT / data / self-context / timeline).
TIMELINE_DIR = paths.SELF_CONTEXT_DIR / "timeline"
CONTEXT_MAX_CHARS = 300

HEADER_COMMENT = """\
# 자사 언급 외부 기사 타임라인 (분기별)
# 기계 축적 파일 — accumulate-self-context.py 가 PR 모니터링 결과에서 append.
# frame/기록이유 등 해석 필드는 담당자/월간 패턴 관찰(patterns-observed.md) 영역.
"""


def quarter_of(d: Date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def load_timeline(path: Path, quarter: str) -> dict:
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("quarter", quarter)
        data.setdefault("entries", [])
        return data
    return {"quarter": quarter, "entries": []}


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else Date.today().isoformat()
    run_date = Date.fromisoformat(date_str)

    # Plugin re-anchor: PR CSV lives under PROJECT_DIR output/pr (paths.PR_OUTPUT_DIR),
    # replacing ref accumulate-self-context.py:55 (PROJECT_ROOT / data / output / pr).
    csv_path = paths.PR_OUTPUT_DIR / f"pr-monitoring-{date_str}.csv"
    if not csv_path.exists():
        print(f"· PR CSV 없음 ({csv_path.name}) — 타임라인 축적 skip")
        return

    quarter = quarter_of(run_date)
    out_path = TIMELINE_DIR / f"{quarter}.yaml"
    TIMELINE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_timeline(out_path, quarter)

    seen_urls = {e.get("url", "") for e in data["entries"] if e.get("url")}
    seen_keys = {(e.get("date", ""), e.get("title", "")) for e in data["entries"]}

    added = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            url = (row.get("URL") or "").strip()
            title = (row.get("제목") or "").strip()
            art_date = (row.get("날짜") or date_str).strip()
            if not title:
                continue
            if (url and url in seen_urls) or (art_date, title) in seen_keys:
                continue
            entry = {
                "date": art_date,
                "source": (row.get("매체") or "").strip(),
                "title": title,
                "tone": (row.get("톤") or "").strip(),
                "mention": (row.get("언급유형") or "").strip(),
                "context": (row.get("맥락") or "").strip()[:CONTEXT_MAX_CHARS],
                "url": url,
            }
            data["entries"].append(entry)
            seen_urls.add(url)
            seen_keys.add((art_date, title))
            added += 1

    if added:
        data["entries"].sort(key=lambda e: e.get("date", ""))
        body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                              default_flow_style=False, width=120)
        out_path.write_text(HEADER_COMMENT + body, encoding="utf-8")
    print(f"✓ timeline {quarter}: {added}건 추가 (누적 {len(data['entries'])}건)")


if __name__ == "__main__":
    main()
