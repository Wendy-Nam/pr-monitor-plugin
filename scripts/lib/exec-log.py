#!/usr/bin/env python3
"""
exec-log.py — 실행 메트릭 로깅 (CLAUDE.md 섹션 6 준수)

run-newsletter.sh / run-pr-monitor.sh 종료 시(trap EXIT) 호출되어
logs/executions/{YYYY-MM-DD}-{run_id}.json 에 기록:
  - 실행 시작/종료 시각, 종료 상태
  - 수집 URL 수, 처리 성공/실패 수
  - 모델별 토큰 수 + 추정 비용 (claude stream-json 로그가 있을 때)
  - 생성된 산출물 경로

사용:
  exec-log.py --pipeline newsletter --date 2026-06-12 --run-id 093015 \
              --started 2026-06-12T09:30:15+09:00 --status 0 --hours 48 \
              [--claude-log logs/executions/newsletter-*.log] [--output <경로>]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths

# Re-anchored for the plugin model: raw/processed live under PLUGIN_DATA,
# the execution log dir lives under PROJECT_DIR (paths.LOGS_DIR already ends in
# "logs/executions"). In dev all roots collapse to repo root, so this matches the
# original layout (data/raw, data/processed, logs/executions) byte-for-byte.
RAW_DIR = paths.RAW_DIR
PROCESSED_DIR = paths.PROCESSED_DIR
LOG_DIR = paths.LOGS_DIR


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def count_urls(date_str: str) -> int | None:
    d = load_json(RAW_DIR / f"urls-{date_str}.json")
    if d is None:
        return None
    if "clusters" in d:
        return sum(c.get("article_count", 1) for c in d["clusters"])
    return len(d.get("urls", []))


def extraction_stats(date_str: str) -> dict | None:
    d = load_json(PROCESSED_DIR / f"extracted-{date_str}.json")
    if d is None:
        return None
    return {"total": d.get("total"), "success": d.get("success"), "failed": d.get("failed")}


def classification_stats(date_str: str) -> dict | None:
    d = load_json(PROCESSED_DIR / f"classified-{date_str}.json")
    if d is None:
        return None
    return {
        "classified": d.get("classified"),
        "pr_articles": d.get("pr_articles"),
        "excluded": d.get("excluded"),
    }


def parse_claude_log(path: Path) -> dict | None:
    """claude -p --output-format stream-json 로그에서 비용/토큰 추출.

    마지막 type=result 이벤트의 total_cost_usd / usage / modelUsage 사용.
    포맷이 다르거나 없으면 None (로깅 자체는 계속).
    """
    if not path.is_file():
        return None
    result_event = None
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "result":
                    result_event = ev
    except OSError:
        return None
    if result_event is None:
        return None
    usage = result_event.get("usage") or {}
    return {
        "cost_usd": result_event.get("total_cost_usd") or result_event.get("cost_usd"),
        "duration_ms": result_event.get("duration_ms"),
        "num_turns": result_event.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "per_model": result_event.get("modelUsage"),  # 모델별 입출력 분리 (있을 때)
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True, choices=["newsletter", "pr_monitoring"])
    ap.add_argument("--date", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--started", required=True)
    ap.add_argument("--status", type=int, default=0)
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--claude-log", default=None)
    ap.add_argument("--output", action="append", default=[],
                    help="생성된 산출물 경로 (반복 지정 가능)")
    ap.add_argument("--pr-count", type=int, default=None)
    args = ap.parse_args()

    record = {
        "pipeline": args.pipeline,
        "date": args.date,
        "run_id": args.run_id,
        "started_at": args.started,
        "ended_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "exit_status": args.status,
        "hours_window": args.hours,
        "urls_collected": count_urls(args.date),
        "extraction": extraction_stats(args.date),
        "classification": classification_stats(args.date),
        "pr_count": args.pr_count,
        "llm": parse_claude_log(Path(args.claude_log)) if args.claude_log else None,
        "outputs": [p for p in args.output if Path(p).is_file()],
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = LOG_DIR / f"{args.date}-{args.run_id}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"실행 로그: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
