"""PR 일일 일괄 실행 — Python port of scripts/pr/run-pr-daily.sh.

Routines 진입점. 전처리(pre) → PR 모니터(pr_monitor) 순서를 보장하는 얇은
오케스트레이터. 수집창은 config/pipelines.yaml 의 pr_monitoring 정책으로
자동 해석한다(평일 hours, 월요일 monday_hours) — 호출부에 시간을 박지 않는다.

Ported from run-pr-daily.sh:
  L11  set -euo pipefail            → step 실패 시 즉시 중단 (rc != 0 early return)
  L17  DATE="${1:-$(date +%F)}"     → args.date (dispatcher 가 오늘 날짜로 기본 채움)
  L18  HOURS=$(resolve_hours pr_monitoring)
  L20  log "=== PR 모니터링 일괄 실행 ($DATE, ${HOURS}h) ==="
  L22  run-pre.sh "$DATE" --hours "$HOURS"   → pre.run (date + hours 공유)
  L23  run-pr-monitor.sh "$DATE"            → pr_monitor.run (date 만; hours 재해석)

run-pr-monitor.sh 는 HOURS 를 인자로 받지 않고 내부에서 다시
resolve_hours pr_monitoring 으로 해석한다(L24-28). 동일 정책이므로 결과는
같다 — 여기서도 pre 에만 hours 를 넘기고 pr_monitor 에는 date 만 넘긴다.
"""
from __future__ import annotations

from argparse import Namespace

from ..common import log, resolve_hours


def run(args) -> int:
    # Lazy sibling import (mirrors __main__.main which imports steps inside the
    # function) so importing this module never hard-depends on pre/pr_monitor.
    from . import pre, pr_monitor

    date = args.date
    # 시간창: 인자로 명시되면 그것을, 없으면 정책(평일 hours·월요일 monday_hours).
    # 매일 안 돌리는 사용자가 수집창을 직접 넓힐 수 있게 override 를 받는다.
    hours = getattr(args, "hours", None)
    if hours is None:
        hours = resolve_hours("pr_monitoring")
    hours = int(hours)

    log(f"=== PR 모니터링 일괄 실행 ({date}, {hours}h) ===")  # L20

    # L22: run-pre.sh "$DATE" --hours "$HOURS" — date + 해석된 hours 공유.
    rc = pre.run(Namespace(date=date, hours=hours))
    if rc != 0:  # set -euo pipefail → 첫 실패에서 중단
        return rc

    # 같은 hours 를 pr_monitor 에도 넘긴다(override 일관성). 인자 없으면 pr_monitor 가
    # 동일 정책으로 재해석하므로 결과는 같다.
    return pr_monitor.run(Namespace(date=date, hours=hours))


if __name__ == "__main__":
    raise SystemExit(run(Namespace(date=None)))
