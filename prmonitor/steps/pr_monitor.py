"""PR Monitor step — Python port of ``scripts/pr/run-pr-monitor.sh``.

Faithful, behavior-preserving port of the bash orchestrator. Each block cites the
``.sh`` line numbers it ports (numbers refer to the ground-truth copy at
``ref-pr-monitor/scripts/pr/run-pr-monitor.sh``, identical to the in-repo copy).

Pipeline (self-PR clipping harness):
  Step A  require extracted-{date}.json  (precondition; run-pre.sh must run first)   .sh 49-53
  Step B  gen-pr-monitor.py {date} {hours}  → pr-monitoring-{date}.html + .csv       .sh 55-72
  Step C  accumulate-pr.py {month}          → pr-monthly-{month}.csv (best-effort)   .sh 74-85
  Step C-2 accumulate-self-context.py {date} → timeline append (deterministic)       .sh 87-93
  Step D  send_html_email(marketing group) + monthly-slice xlsx attachment           .sh 95-105
  exec-log written in try/finally (was ``trap 'write_exec_log $?' EXIT``)             .sh 31-44

Three-root differences vs the .sh (per the architecture contract):
  - ``$PY scripts/pr/foo.py`` (cwd=PROJECT_ROOT, relative argv) becomes a
    ``subprocess.run([venv_python, paths.SCRIPTS_DIR/'pr'/'foo.py', ...])`` call
    with explicit absolute argv. No bash, no shell=True, no cwd reliance.
  - Output paths come from ``paths`` (PR_OUTPUT_DIR / PROCESSED_DIR), not the flat
    ``data/output/pr`` the bash assumed.
  - ``date +...`` / ``wc -l`` / ``${DATE:0:7}`` / ``${VAR//x/y}`` are reimplemented
    in pure Python.

run(args) reads ``args.date`` (the dispatcher fills it with today when omitted).
There is no ``--no-email`` flag on the ``pr-monitor`` subparser (see
prmonitor.__main__), so the .sh's ``$3 == --no-email`` skip path is unreachable
here and is intentionally not wired in; email always goes through
common.send_html_email, which itself no-ops gracefully when delivery.yaml/auth is
absent (matching the .sh's "artifact already written" failure policy).
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from .. import domainpack, paths
from ..common import (
    err,
    log,
    ok,
    pipeline_cfg,
    require_file,
    resolve_hours,
    send_html_email,
    warn,
    cleanup_retention,
)

PIPELINE = "pr_monitoring"


def _run_step_script(script_rel: str, *script_args: str) -> int:
    """Invoke a Python step-script with the venv interpreter (explicit argv).

    Ports the bash ``"$PY" scripts/pr/<name>.py <args> 2>&1`` invocations. Output
    is inherited (not captured) so the child's stderr/stdout still reaches the
    console, mirroring the ``2>&1`` passthrough. Returns the child exit code; -1
    if the interpreter could not be launched (OSError), so callers can branch the
    same way the bash ``if ! ...; then`` did.
    """
    argv = [str(paths.venv_python()), str(paths.SCRIPTS_DIR / script_rel), *script_args]
    try:
        r = subprocess.run(argv, check=False)
    except OSError as e:
        err(f"{script_rel} 실행 불가 — {e}")
        return -1
    return r.returncode


def _count_csv_rows(csv_path: Path) -> int:
    """PR 건수 = CSV 행 수 - 1 (헤더). 음수면 0. Ports .sh 67-72 (``wc -l`` - 1)."""
    if not csv_path.is_file():
        return 0
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        return 0
    pr_count = line_count - 1
    return pr_count if pr_count > 0 else 0


def _write_exec_log(*, date: str, run_id: str, started_at: str, status: int,
                    hours: int, pr_count: int, html: Path, csv: Path) -> None:
    """Port of the .sh ``write_exec_log`` (.sh 35-43): call scripts/lib/exec-log.py.

    Records on success OR failure (trap EXIT semantics). A failure to write the
    log itself only warns (``|| warn`` in the .sh), never propagates.
    """
    argv = [
        str(paths.venv_python()), str(paths.SCRIPTS_DIR / "lib" / "exec-log.py"),
        "--pipeline", PIPELINE, "--date", date, "--run-id", run_id,
        "--started", started_at, "--status", str(status), "--hours", str(hours),
        "--pr-count", str(pr_count),
        "--output", str(html),
        "--output", str(csv),
    ]
    try:
        r = subprocess.run(argv, check=False)
        if r.returncode != 0:
            warn("실행 로그 기록 실패")
    except OSError:
        warn("실행 로그 기록 실패")


def run(args) -> int:
    """Run the PR monitoring pipeline. Returns 0 on success, nonzero on failure.

    Mirrors ``set -euo pipefail`` + ``trap 'write_exec_log $?' EXIT``: every exit
    path (success, Step A/B failure) writes the exec-log with the final status
    via the ``finally`` block.
    """
    # ── 인자 파싱 (.sh 16-19) — DATE from args.date; no --no-email flag here ──
    date = args.date

    # 수집 윈도우 (.sh 23-28): config 기반, 월요일은 monday_hours (주말 커버).
    # 2번째 인자 override 는 pr-monitor 서브파서에 없음 → 항상 정책값.
    hours = resolve_hours(PIPELINE)
    log_hours_note = ""
    if datetime.now().weekday() == 0:  # Monday (date +%u == 1)
        log_hours_note = f"월요일 감지 — {hours}h (주말 커버)"

    # data/output/pr 보장 (.sh 29) — three-root: PR_OUTPUT_DIR.
    paths.PR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 실행 메트릭 로깅 준비 (.sh 31-34) ──
    # RUN_ID=date +%H%M%S, STARTED_AT=date +%FT%T%z — pure-Python equivalents.
    run_id = datetime.now().strftime("%H%M%S")
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    pr_count = 0

    pr_html = paths.PR_OUTPUT_DIR / f"pr-monitoring-{date}.html"
    pr_csv = paths.PR_OUTPUT_DIR / f"pr-monitoring-{date}.csv"

    # trap 'write_exec_log $?' EXIT (.sh 44) → try/finally. status tracks the
    # would-be exit code so the log records success/failure like $? did.
    status = 0
    try:
        log(f"=== PR 모니터링 시작 ({date}, {hours}h) ===")  # .sh 46
        if log_hours_note:  # .sh 47
            log(log_hours_note)

        # ── Step A: 사전 조건 확인 (.sh 49-53) ──
        extracted = paths.PROCESSED_DIR / f"extracted-{date}.json"
        try:
            require_file(
                extracted,
                f"Step A: {extracted} 없음.\n"
                f"  → 먼저 run-pre.sh 실행: ./scripts/pipeline/run-pre.sh {date}",
            )
        except SystemExit as e:  # require_file raises SystemExit(1)
            status = int(e.code) if isinstance(e.code, int) else 1
            return status
        log("Step A: 전처리 데이터 확인 OK")  # .sh 53

        # ── Step B: PR 모니터링 HTML + CSV 생성 (.sh 55-65) ──
        log("Step B: 자사 언급 기사 추출 + HTML/CSV 생성...")  # .sh 59
        rc = _run_step_script("pr/gen-pr-monitor.py", date, str(hours))  # .sh 60
        if rc != 0:
            err("Step B: gen-pr-monitor.py 실패")  # .sh 61
            status = 1
            return status  # .sh 62 (exit 1)
        try:
            require_file(pr_html, f"Step B: {pr_html} 미생성")  # .sh 64
        except SystemExit as e:
            status = int(e.code) if isinstance(e.code, int) else 1
            return status
        ok("Step B: PR 모니터링 생성 완료")  # .sh 65

        # 건수 파악 (.sh 67-72): CSV 행 수 - 1.
        pr_count = _count_csv_rows(pr_csv)

        # ── Step C: PR 월별 누적 (.sh 74-85) ──
        monthly = date[:7]  # ${DATE:0:7}
        if pr_csv.is_file():  # .sh 76
            log(f"Step C: PR 월별 누적 ({monthly})...")  # .sh 77
            if _run_step_script("pr/accumulate-pr.py", monthly) == 0:  # .sh 78
                ok(f"Step C: PR 누적 완료 → "
                   f"{paths.PR_OUTPUT_DIR / f'pr-monthly-{monthly}.csv'}")  # .sh 79
            else:
                warn("Step C: PR 누적 실패 (리포트는 정상 생성됨)")  # .sh 81
        else:
            log("Step C: PR CSV 없음 (자사 언급 0건)")  # .sh 84

        # ── Step C-2: 자사 맥락 타임라인 축적 (결정론, LLM 없음) (.sh 87-93) ──
        log("Step C-2: 자사 맥락 타임라인 축적...")  # .sh 88
        if _run_step_script("pr/accumulate-self-context.py", date) == 0:  # .sh 89
            ok("Step C-2: 타임라인 축적 완료")  # .sh 90
        else:
            warn("Step C-2: 타임라인 축적 실패 (리포트는 정상 생성됨)")  # .sh 92

        # ── Step D: 이메일 발송 (.sh 95-105) ──
        email_group = pipeline_cfg(PIPELINE, "email_group", "marketing_pr_list")  # .sh 96
        # 폴백 제목의 조직명은 branding 도메인팩에서 (없으면 중립 빈 문자열).
        # 1차값은 여전히 pipeline_cfg(PIPELINE, "subject") 에서 온다.
        org_name = domainpack.get("branding", "org_name", "")
        subject_tpl = pipeline_cfg(
            PIPELINE, "subject",
            f"[PR 모니터링] {org_name} 자사 언급 ({{date}}) - {{count}}건",
        )  # .sh 97
        # ${SUBJECT_TPL//{date}/$DATE} ; ${...//{count}/$PR_COUNT}  (.sh 98-99)
        subject = subject_tpl.replace("{date}", date).replace("{count}", str(pr_count))
        pr_xlsx = paths.PR_OUTPUT_DIR / f"pr-monitoring-{date}.xlsx"  # .sh 100
        # NO_EMAIL skip path (.sh 101-102) is unreachable here (no --no-email flag).
        send_html_email(email_group, subject, pr_html, pr_xlsx)  # .sh 104

        # ── 완료 (.sh 107-114) ──
        cleanup_retention()  # .sh 108
        ok(f"PR 모니터링 완료 (자사 언급 {pr_count}건)")  # .sh 110
        log(f"  리포트: {pr_html}")  # .sh 112
        if pr_csv.is_file():  # .sh 113
            log(f"  CSV: {pr_csv}")
        status = 0
        return status
    finally:
        # trap 'write_exec_log $?' EXIT (.sh 44) — runs on every exit path.
        _write_exec_log(
            date=date, run_id=run_id, started_at=started_at, status=status,
            hours=hours, pr_count=pr_count, html=pr_html, csv=pr_csv,
        )


if __name__ == "__main__":
    import argparse

    _ap = argparse.ArgumentParser()
    _ap.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    raise SystemExit(run(_ap.parse_args()))
