"""뉴스레터 후처리 (Steps 8-10) — faithful Python port of
``scripts/newsletter/run-post.sh``.

Pipeline: resolve-refs → format.py → update-landscape → quality gate → email.
Called after Step 7 (insight synthesis) produces the briefing JSON.

This module ports ``ref-pr-monitor/scripts/newsletter/run-post.sh`` line-for-line.
Cited .sh line numbers appear inline next to each ported block. Behaviour
preserved exactly: the briefing-exists precondition, resolve-refs rc==2 hold
semantics, the deterministic domestic/foreign count, skip-if-format-fails
exit, the quality gate (warnings > 5 OR unresolved refs > 30% => hold +
REVIEW_NEEDED.md), exit-on-failure step sequencing, exec-logging (trap EXIT
→ try/finally), and retention cleanup.

Notable contract-driven deltas from the .sh (behaviour identical, paths only):
  - All ``data/processed`` and ``data/output`` literals become the three-root
    dirs: processed/raw under PLUGIN_DATA (paths.PROCESSED_DIR), output under
    PROJECT_DIR (paths.NEWSLETTER_OUTPUT_DIR / paths.OUTPUT_DIR).
  - ``$PY`` → ``paths.venv_python()``; format.py lives at
    ``paths.SKILLS_DIR/"briefing-formatter"/"format.py"`` (was
    ``.claude/skills/...``). resolve-refs.py / update-landscape.py / exec-log.py
    live under ``paths.SCRIPTS_DIR``.
  - No bash/awk/date/trap: subprocess.run([...]) with explicit argv,
    datetime/time for timestamps, try/finally for the EXIT trap.
  - The inline ``"$PY" -c`` count + quality-warning reads (.sh lines 72-95,
    126-132) are computed in-process here (same logic, no subprocess).
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime

from .. import paths
from ..common import (
    err,
    load_json,
    log,
    ok,
    pipeline_cfg,
    require_file,
    send_html_email,
    warn,
    cleanup_retention,
)

QW_THRESHOLD = 5  # .sh:125 — 품질 경고 임계


def _compute_counts(date_str: str) -> tuple[int, int, str]:
    """국내/해외 기사 수 + 수집 시작일 — ports the inline ``"$PY" -c`` (.sh:72-95).

    Reads newsletter-facts (briefing의 all_sources 폐지 — facts가 전체 기사 원천).
    .kr TLD URL 또는 title 한글 포함 → 국내. 모든 실패는 (0, 0, date_str) 폴백
    (.sh:91 ``|| echo "0 0 ${DATE}"``).
    """
    facts_path = paths.PROCESSED_DIR / f"newsletter-facts-{date_str}.json"
    try:
        d = load_json(facts_path)
        sources = [
            a
            for cat in d.get("categories", [])
            for a in cat.get("facts", [])
        ]

        def is_domestic(s: dict) -> bool:
            url = s.get("source_url", "") or ""
            title = s.get("title", "") or ""
            if ".kr/" in url or url.endswith(".kr"):
                return True
            if any("가" <= c <= "힣" for c in title):
                return True
            return False

        domestic = sum(1 for s in sources if is_domestic(s))
        foreign = len(sources) - domestic
        dates = [s.get("source_date", "") for s in sources if s.get("source_date")]
        start = min(dates) if dates else date_str
        return foreign, domestic, start
    except Exception:
        return 0, 0, date_str  # .sh:91 폴백


def _quality_warning_count(date_str: str) -> int:
    """품질 경고 JSON 길이 — ports the inline ``"$PY" -c`` (.sh:126-132).

    파일 없음/파싱 실패 → 0 (.sh:131 ``except: print(0)``).
    """
    qw_file = paths.NEWSLETTER_OUTPUT_DIR / f".quality-warnings-{date_str}.json"
    try:
        return len(load_json(qw_file))
    except Exception:
        return 0


def _write_exec_log(
    date_str: str,
    hours: int,
    run_id: str,
    started_at: str,
    status: int,
    briefing,
    html_out,
) -> None:
    """exec-log.py 호출 — ports write_exec_log() (.sh:32-40).

    PR_MONITOR_EXEC_LOGGED=1 이면 헤드리스 경로 trap이 전담 → skip (.sh:33).
    """
    import os

    if os.environ.get("PR_MONITOR_EXEC_LOGGED") == "1":  # .sh:33
        return
    argv = [
        str(paths.venv_python()),
        str(paths.SCRIPTS_DIR / "lib" / "exec-log.py"),
        "--pipeline", "newsletter",
        "--date", date_str,
        "--run-id", run_id,
        "--started", started_at,
        "--status", str(status),
        "--hours", str(hours),
        "--output", str(briefing),
        "--output", str(html_out),
    ]
    try:
        r = subprocess.run(argv, check=False)
    except OSError:
        warn("실행 로그 기록 실패")  # .sh:39
        return
    if r.returncode != 0:
        warn("실행 로그 기록 실패")  # .sh:39


def run(args) -> int:
    """후처리 실행. 0 = 성공, nonzero = 실패. (run-post.sh main body)"""
    # ── 인자 (.sh:20-23) — date/hours from dispatcher; --no-email via attr ──
    date_str = args.date
    hours = args.hours if getattr(args, "hours", None) is not None else 24  # .sh:21
    no_email = bool(getattr(args, "no_email", False))  # .sh:22-23

    # ensure_venv는 dispatcher(__main__)가 이미 수행. 출력 디렉터리 보장 (.sh:26).
    paths.NEWSLETTER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 실행 메트릭 로깅 준비 (.sh:30-31) ──
    run_id = time.strftime("%H%M%S")          # .sh:30  date +%H%M%S
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")  # .sh:31

    # 발송 보류 사유 누적 (품질 게이트) — 비면 정상 발송 (.sh:44)
    hold_reasons: list[str] = []

    # 산출물 경로 (.sh:49-50). 3-root: processed→PLUGIN_DATA, output→PROJECT_DIR.
    briefing = paths.BRIEFING_DIR / f"newsletter-briefing-{date_str}.json"
    html_out = paths.NEWSLETTER_OUTPUT_DIR / f"newsletter-report-{date_str}.html"

    status = 1  # trap EXIT는 $? 를 기록 — 성공 경로에서 0으로 설정 (.sh:41)
    try:
        log(f"=== PR Monitor 후처리 시작 ({date_str}) ===")  # .sh:46

        # ── Step 8 precondition: briefing 존재 (.sh:52-57) ──
        if not briefing.is_file():
            err(f"Step 8: {briefing} 없음.")  # .sh:53
            err("  → Step 7 (인사이트 합성)이 완료되지 않았습니다.")  # .sh:54
            err("  → @insight-synthesizer 에이전트가 briefing JSON을 먼저 생성해야 합니다.")  # .sh:55
            return 1  # .sh:56 exit 1

        # ── Step 8-pre: resolve-refs (.sh:60-68) ──
        log("Step 8-pre: 출처 ref 해석 (resolve-refs)...")  # .sh:60
        resolve_rc = 0
        try:
            rr = subprocess.run(
                [str(paths.venv_python()),
                 str(paths.SCRIPTS_DIR / "newsletter" / "resolve-refs.py"),
                 date_str],
                check=False,
            )  # .sh:62
            resolve_rc = rr.returncode
        except OSError as e:
            resolve_rc = 1
            warn(f"resolve-refs 실행 실패 — {e}")
        if resolve_rc == 2:  # .sh:63
            warn("resolve-refs: 미해결 출처 비율 > 30% — 발송 보류 대상")  # .sh:64
            hold_reasons.append(
                "출처 ref 미해결 비율 > 30% — 인라인 출처 [n] 매칭 손상 가능")  # .sh:65
        elif resolve_rc != 0:  # .sh:66
            warn(f"resolve-refs 실패 (rc={resolve_rc}) — 기존 briefing 그대로 진행")  # .sh:67

        # ── Step 8: 국내/해외 기사 수 자동 계산 (.sh:71-95) ──
        log("Step 8: 국내/해외 기사 수 자동 계산...")  # .sh:71
        foreign, domestic, collection_start = _compute_counts(date_str)

        # 직전 발행 마커 (.sh:97-99) — PREV_RUN 은 .sh 에서도 미사용이라 읽기만.
        last_run_file = paths.NEWSLETTER_OUTPUT_DIR / ".last-newsletter-run"

        # ── Step 8: HTML 생성 (.sh:101-113) ──
        log(f"Step 8: HTML 생성 (해외 {foreign}건, 국내 {domestic}건)...")  # .sh:101
        facts_path = paths.PROCESSED_DIR / f"newsletter-facts-{date_str}.json"
        fmt_argv = [
            str(paths.venv_python()),
            str(paths.SKILLS_DIR / "briefing-formatter" / "format.py"),
            "--input", str(briefing),
            "--date", date_str,
            "--output", str(html_out),
            "--collection-start", collection_start,
            "--foreign", str(foreign),
            "--domestic", str(domestic),
            "--hours", str(hours),
            "--facts", str(facts_path),
        ]  # .sh:102-110
        try:
            fmt = subprocess.run(fmt_argv, check=False)
        except OSError as e:
            err(f"Step 8: format.py 실행 실패 — {e}")
            return 1
        if fmt.returncode != 0:  # .sh:102 (if ! ...)
            err("Step 8: format.py 실패")  # .sh:111
            return 1  # .sh:112 exit 1

        # 발행 시각 기록 (.sh:115-116) — date "+%Y-%m-%d %H:%M"
        last_run_file.write_text(
            datetime.now().strftime("%Y-%m-%d %H:%M") + "\n", encoding="utf-8")

        require_file(html_out, f"Step 8: {html_out} 미생성")  # .sh:118

        html_size = html_out.stat().st_size  # .sh:120  wc -c
        ok(f"Step 8: HTML 생성 완료 ({html_size} bytes)")  # .sh:121

        # ── Step 8-post: 품질 경고 게이트 (.sh:124-136) ──
        qw_count = _quality_warning_count(date_str)  # .sh:126-132
        qw_file = paths.NEWSLETTER_OUTPUT_DIR / f".quality-warnings-{date_str}.json"
        if qw_count > QW_THRESHOLD:  # .sh:133
            warn(f"품질 경고 {qw_count}건 > {QW_THRESHOLD}건 — 발송 보류 대상")  # .sh:134
            hold_reasons.append(
                f"품질 경고 {qw_count}건 (임계 {QW_THRESHOLD}건 초과) — {qw_file} 확인")  # .sh:135

        # ── Step 9: competitor-landscape 자동 갱신 (.sh:138-141) ──
        log("Step 9: competitor-landscape.yaml 갱신 확인...")  # .sh:139
        try:
            subprocess.run(
                [str(paths.venv_python()),
                 str(paths.SCRIPTS_DIR / "pipeline" / "update-landscape.py"),
                 date_str],
                check=False,
            )  # .sh:140  (실패해도 계속 — `|| true`)
        except OSError as e:
            warn(f"update-landscape 실행 실패 (후처리는 계속) — {e}")  # .sh:141

        # ── Step 10: 이메일 발송 (.sh:145-174) ──
        source_count = foreign + domestic  # .sh:146
        email_group = pipeline_cfg("newsletter", "email_group", "newsletter_briefing")  # .sh:147
        subject_tpl = pipeline_cfg(
            "newsletter", "subject",
            "[뉴스레터] 로봇 산업군 동향 및 인사이트 ({date}) - 출처 {count}건")  # .sh:148
        subject = subject_tpl.replace("{date}", date_str)  # .sh:149
        subject = subject.replace("{count}", str(source_count))  # .sh:150

        send_disabled = not pipeline_cfg("newsletter", "send_email", True)  # 상시 발송 차단 토글
        if no_email or send_disabled:  # .sh:151
            why = "--no-email" if no_email else "pipelines.yaml send_email: false"
            log(f"Step 10: {why} — 발송 skip")  # .sh:152
        elif hold_reasons:  # .sh:153  ${#HOLD_REASONS[@]} > 0
            # 품질 게이트 발동 → 발송 보류 + REVIEW_NEEDED.md (.sh:154-171)
            review_file = paths.OUTPUT_DIR / "REVIEW_NEEDED.md"  # .sh:155
            review_file.parent.mkdir(parents=True, exist_ok=True)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")  # .sh:159
            lines = [
                "# 발송 보류 — 사용자 확인 필요",  # .sh:157
                "",
                f"- 일시: {now_str}",  # .sh:159
                f"- 대상: {html_out}",  # .sh:160
                "",
                "## 사유",  # .sh:162
            ]
            lines += [f"- {r}" for r in hold_reasons]  # .sh:163
            lines += [
                "",
                "## 확인 후 수동 발송",  # .sh:165
                "```bash",  # .sh:166
                f'python3 "${{CLAUDE_PLUGIN_ROOT}}/prmonitor_launch.py" post {date_str} {hours}',
                "```",  # .sh:168
                "(브리핑 JSON 수정 후 재실행하면 HTML 재생성 + 게이트 재평가)",  # .sh:169
            ]
            review_file.write_text("\n".join(lines) + "\n", encoding="utf-8")  # .sh:170
            warn(f"Step 10: 품질 게이트 발동 — 발송 보류. {review_file} 확인.")  # .sh:171
        else:  # .sh:172
            send_html_email(email_group, subject, html_out)  # .sh:173

        # ── 완료 (.sh:176-182) ──
        cleanup_retention()  # .sh:177
        ok("후처리 완료")  # .sh:179
        log(f"  📄 {html_out}")  # .sh:181

        status = 0  # 성공 — trap EXIT 가 기록할 종료 상태 (.sh:41)
        return 0
    finally:
        # trap 'write_exec_log $?' EXIT (.sh:41) — 성공/실패 모두 기록.
        _write_exec_log(date_str, hours, run_id, started_at, status, briefing, html_out)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("hours", type=int, nargs="?", default=24)
    ap.add_argument("--no-email", dest="no_email", action="store_true")
    raise SystemExit(run(ap.parse_args()))
