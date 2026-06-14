"""`python -m prmonitor <subcommand>` — the cross-platform CLI that replaces the
bash orchestrators (run-pre/post/pr-monitor/pr-daily/newsletter.sh + common.sh).

Each subcommand delegates to a module under :mod:`prmonitor.steps`, which exposes
``run(args) -> int``. The dispatcher owns only arg parsing and venv bootstrap;
all pipeline logic lives in the step modules (faithful ports of the .sh files).

Subcommand map:
  pre <date> [--hours N]    ← run-pre.sh         (fetch→extract→classify→aggregate→preload)
  post <date> <hours>       ← run-post.sh        (resolve-refs→format→landscape→gate→email)
  pr-monitor <date>         ← run-pr-monitor.sh  (gen-pr→accumulate→email)
  pr [date]                 ← run-pr-daily.sh    (pre + pr-monitor)
  newsletter [--hours N]    ← run-newsletter.sh  (pre → claude -p synth → post)
  init                      ← SessionStart scaffolding (no venv required first)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prmonitor", description="PR Monitor pipeline CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("pre", help="공통 전처리 (수집→추출→분류→집계→컨텍스트)")
    p_pre.add_argument("date", nargs="?", default=None)
    p_pre.add_argument("--hours", type=int, default=None)

    p_post = sub.add_parser("post", help="뉴스레터 후처리 (렌더→게이트→발송)")
    p_post.add_argument("date", nargs="?", default=None)
    p_post.add_argument("hours", type=int, nargs="?", default=None)

    p_prmon = sub.add_parser("pr-monitor", help="PR 모니터링 (톤판정→누적→발송)")
    p_prmon.add_argument("date", nargs="?", default=None)

    p_pr = sub.add_parser("pr", help="PR 일일 (pre + pr-monitor)")
    p_pr.add_argument("date", nargs="?", default=None)

    p_nl = sub.add_parser("newsletter", help="뉴스레터 (pre → 합성 → post)")
    p_nl.add_argument("date", nargs="?", default=None)
    p_nl.add_argument("--hours", type=int, default=None)

    sub.add_parser("init", help="첫 실행 스캐폴딩 (config/data 골격 + venv)")
    sub.add_parser("paths", help="해석된 경로 출력 (디버그)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if getattr(args, "date", None) is None and args.cmd in {"pre", "post", "pr-monitor", "pr", "newsletter"}:
        args.date = _today()

    if args.cmd == "paths":
        from . import paths
        print(paths.as_exports())
        return 0

    if args.cmd == "init":
        from .steps import init
        return init.run(args)

    # Pipeline subcommands need the venv (deps). Bootstrap is idempotent.
    from . import bootstrap
    try:
        bootstrap.ensure_venv(quiet=False)
    except bootstrap.BootstrapError as e:
        print(f"[bootstrap] {e}", file=sys.stderr)
        return 1

    from .steps import pre, post, pr_monitor, pr_daily, newsletter
    dispatch = {
        "pre": pre.run, "post": post.run, "pr-monitor": pr_monitor.run,
        "pr": pr_daily.run, "newsletter": newsletter.run,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
