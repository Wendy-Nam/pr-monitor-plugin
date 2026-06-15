"""Newsletter orchestrator — Python port of scripts/newsletter/run-newsletter.sh.

Three-stage pipeline, faithful to the bash ground truth
(ref-pr-monitor/scripts/newsletter/run-newsletter.sh):

    1. pre.run     — URL 수집 → 추출 → 분류 → 집계 → 컨텍스트 (결정론적)   [.sh L66-70]
    2. claude -p   — 인사이트 합성 (Step 7, LLM)                          [.sh L78-126]
    3. post.run    — HTML 렌더 → PR 누적 → 이메일                         [.sh L128-136]

Faithfully ported behaviors (with .sh line cites inline):
  - Collection window: args.hours > resolve_hours("newsletter")            [.sh L45-46]
  - Exec-metric logging on EXIT regardless of status (the bash `trap`)     [.sh L50-62]
  - run-pre failure → exit 1                                               [.sh L66-70]
  - --dry-run short-circuit after pre (skip claude)                        [.sh L72-76]
  - Guard: missing `claude` binary → clear error, exit 1                   [.sh L78-82]
  - The synthesis prompt heredoc, re-anchored to ABSOLUTE plugin paths     [.sh L84-105]
  - claude exit code is informational only — briefing existence decides    [.sh L111-120]
  - require_file on the briefing JSON → synthesis-failure guard            [.sh L125-126]
  - PR_MONITOR_EXEC_LOGGED=1 so post's own trap stays silent (no dup log)  [.sh L131-132]
  - run-post failure → exit 1                                              [.sh L133-136]
  - REVIEW_NEEDED.md notice                                                [.sh L142-145]
  - Cost summary — parsed from the stream-json log via JSON, NOT grep/awk  [.sh L147-154]

Deviations from the .sh, mandated by the architecture contract:
  - No bash / os.system / shell=True. Each step is subprocess.run([...]) with an
    explicit argv list, or a direct in-process call to the sibling step module.
  - Paths are 3-root absolute (prmonitor.paths), never `cd`+relative. The prompt's
    spec/input/output references are re-anchored:
      spec    → paths.AGENTS_DIR / "insight-synthesizer.md"   (was .claude/agents/…)
      input   → paths.PROCESSED_DIR / synthesis-context-{date}.json
      output  → paths.PROCESSED_DIR / newsletter-briefing-{date}.json
      precheck formatter → paths.SKILLS_DIR / briefing-formatter / format.py
  - Cost parsing reuses the exec-log JSON parser (last type=result event's
    total_cost_usd) instead of `grep -o '"cost_usd"' | awk` — the .sh's own
    comment notes the value lives in the stream-json result event.
  - The pre/post stages are invoked as in-process module calls
    (prmonitor.steps.pre.run / .post.run) rather than spawning the .sh, matching
    the dispatcher contract in prmonitor.__main__.

run(args) -> int : 0 = success, nonzero = failure. Uses args.date and
args.hours (default resolve_hours("newsletter")). Optional args.dry_run /
args.no_email mirror the bash flags when present.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from .. import domainpack, paths
from ..common import err, log, ok, require_file, resolve_hours, warn


def _exec_log(*, date: str, run_id: str, started: str, status: int,
              hours: int, claude_log: str) -> None:
    """Write the execution metric record (CLAUDE.md §6).

    Faithful to the bash `write_exec_log` (.sh L53-60): invoke
    scripts/lib/exec-log.py with the venv interpreter, recording status/hours,
    the claude stream-json log, and both output artifacts. Never raises — a
    logging failure only warns, exactly like the bash `|| warn` (.sh L60).
    """
    briefing = paths.PROCESSED_DIR / f"newsletter-briefing-{date}.json"
    html = paths.NEWSLETTER_OUTPUT_DIR / f"newsletter-report-{date}.html"
    argv = [
        str(paths.venv_python()),
        str(paths.SCRIPTS_DIR / "lib" / "exec-log.py"),
        "--pipeline", "newsletter",
        "--date", date,
        "--run-id", run_id,
        "--started", started,
        "--status", str(status),
        "--hours", str(hours),
        "--claude-log", claude_log,
        "--output", str(briefing),
        "--output", str(html),
    ]
    try:
        r = subprocess.run(argv, check=False)
        if r.returncode != 0:
            warn("실행 로그 기록 실패")
    except OSError:
        warn("실행 로그 기록 실패")


def _synth_model_from_spec() -> str:
    """insight-synthesizer.md frontmatter 의 `model:` 값. 없으면 sonnet 기본.

    raw `claude -p` 는 frontmatter 를 자동 적용하지 않으므로 여기서 읽어 --model 로 넘긴다.
    """
    default = "claude-sonnet-4-6"
    val = default
    try:
        spec = (paths.AGENTS_DIR / "insight-synthesizer.md").read_text(encoding="utf-8")
        in_fm = False
        for line in spec.splitlines():
            if line.strip() == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm and line.lower().startswith("model:"):
                v = line.split(":", 1)[1].strip().strip('"\'')
                if v:
                    val = v
                break
    except OSError:
        pass
    return val


def _enforce_cheap_model(model: str) -> str:
    """합성 모델을 Sonnet/Haiku 로 강제. Opus 는 비용 과다라 절대 쓰지 않는다.

    frontmatter·PRM_SYNTH_MODEL 무엇이 와도 opus 계열이면 sonnet 으로 강등한다.
    """
    if "opus" in (model or "").lower():
        warn(f"합성 모델 '{model}' → 비용 보호로 claude-sonnet-4-6 강등")
        return "claude-sonnet-4-6"
    return model or "claude-sonnet-4-6"


def _synth_prompt(date: str) -> str:
    """The Step-7 synthesis prompt — ported from the heredoc (.sh L86-104).

    Same instructions, but every relative path is re-anchored to an absolute
    3-root path so the headless `claude` session resolves files regardless of
    its cwd (the plugin model has no single flat root to `cd` into).
    """
    spec = paths.AGENTS_DIR / "insight-synthesizer.md"
    synthesis_ctx = paths.PROCESSED_DIR / f"synthesis-context-{date}.json"
    briefing = paths.PROCESSED_DIR / f"newsletter-briefing-{date}.json"
    blind_spots = paths.SELF_CONTEXT_DIR / "blind-spots.md"
    formatter = paths.SKILLS_DIR / "briefing-formatter" / "format.py"
    precheck_html = f"/tmp/precheck-newsletter-{date}.html"
    org = domainpack.get("branding", "org_name", "")
    dept = domainpack.get("branding", "dept", "")
    return f"""너는 {org} {dept} 팀의 뉴스레터 합성 세션이다.
전처리(URL 수집~컨텍스트 압축)는 이미 완료 — 해당 산출물을 재생성하지 않는다.

1. {spec} 를 읽어라. 합성 규칙(톤·구조·JSON 스키마·자가 검증)의
   단일 정본이다. 이 프롬프트와 충돌하면 스펙을 따른다.
2. 입력은 {synthesis_ctx} 하나만 읽는다
   (tier1 팩트 + tier2 헤드라인 + 자사 맥락 전부 인라인).
   extracted-*.json 과 newsletter-facts 는 읽지 않는다 (스펙의 미분류 승격 예외만 허용).
   {blind_spots} 는 절대 읽지 않는다.
3. 이 세션이 곧 synthesizer 다 — Task/Agent 툴로 서브에이전트를 스폰하지 마라 (토큰 2배).
   스펙대로 이 컨텍스트에서 직접 합성해 {briefing} 에 저장한다.
4. 스펙의 '출력 후 자가 검증'(check-coverage-gaps) 을 수행한 뒤, 품질 사전 점검을 실행한다:
   python3 {formatter} --input {briefing} --date {date} --output {precheck_html}
   (출력은 반드시 이 임시 경로 — 최종 HTML 은 run-post 전담. 출처 조인 전이라 최종 경로에 쓰면 빈 출처 중간본이 노출된다.)
   경고가 나오면 JSON 전체 재생성 금지 — 지목된 필드만 Edit 로 수정 후 재실행 (최대 2회).
   이후 run-post 가 정식 렌더·품질 게이트·발송을 결정론적으로 수행한다.
5. 완료 보고는 기사 수·인사이트 수·잔여 경고 수·생성 파일 경로만. 브리핑 내용을 출력하지 않는다.
"""


def _parse_cost(claude_log: str):
    """Total cost from the stream-json log, via JSON — not grep/awk (.sh L147-154).

    Reuses exec-log.py's parser (last type=result event's total_cost_usd),
    matching the .sh comment that the cost lives in the stream-json result
    event. Returns a float or None when unavailable.
    """
    try:
        from importlib import util as _util
        spec = _util.spec_from_file_location(
            "_prm_exec_log", str(paths.SCRIPTS_DIR / "lib" / "exec-log.py"))
        mod = _util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        info = mod.parse_claude_log(__import__("pathlib").Path(claude_log))
    except Exception:
        return None
    if not info:
        return None
    cost = info.get("cost_usd")
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


def run(args) -> int:
    """Port of run-newsletter.sh main flow. Returns 0 on success, nonzero on failure."""
    date = getattr(args, "date", None) or datetime.now().strftime("%Y-%m-%d")
    # 시간창: 인자 > pipelines.yaml (월요일 보정 포함)               [.sh L45-46]
    hours = getattr(args, "hours", None)
    if hours is None:
        hours = resolve_hours("newsletter")
    hours = int(hours)
    dry_run = bool(getattr(args, "dry_run", False))
    no_email = bool(getattr(args, "no_email", False))

    # Writable dir skeleton (mkdir -p data/raw data/processed …)      [.sh L48]
    paths.ensure_dirs()

    # ── exec-log scaffolding (the bash EXIT trap, .sh L50-62) ──────────────────
    # stream-json log path mirrors logs/executions/newsletter-{F-HM}.log (.sh L27-29)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    output_log = str(paths.LOGS_DIR / f"newsletter-{timestamp}.log")
    run_id = datetime.now().strftime("%H%M%S")
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    # status carries the EXIT code passed to the trap; updated as we go so the
    # log records the real outcome even on an early failure (.sh `trap ... EXIT`).
    status = 0

    def _finish(code: int) -> int:
        # Equivalent of `trap 'write_exec_log $?' EXIT` (.sh L62): always log.
        _exec_log(date=date, run_id=run_id, started=started_at,
                  status=code, hours=hours, claude_log=output_log)
        return code

    log(f"=== 뉴스레터 실행 ({date}, {hours}h) ===")

    # ── Step 1~6: 전처리 (결정론적) — pre.run                       [.sh L66-70] ─
    from . import pre  # lazy: sibling module is a separate port
    pre_args = SimpleNamespace(date=date, hours=hours)
    try:
        pre_rc = pre.run(pre_args)
    except SystemExit as e:  # require_file etc. raise SystemExit(1)
        pre_rc = e.code if isinstance(e.code, int) else 1
    if pre_rc != 0:
        err("전처리(pre) 실패")
        return _finish(1)

    if dry_run:
        log("DRY RUN — Claude 호출 skip (전처리 완료)")          # [.sh L72-76]
        print(f"  data/processed/newsletter-facts-{date}.json")
        return _finish(0)

    # ── Step 7: 인사이트 합성 (Claude) ─────────────────────────── [.sh L78-126] ─
    # Guard missing claude binary with a clear error (.sh L79-82).
    claude_bin = shutil.which("claude")
    if not claude_bin:
        err("claude CLI 없음. https://docs.claude.com 참고해 Claude Code 설치.")
        return _finish(1)

    prompt = _synth_prompt(date)

    log("Step 7: 인사이트 합성 시작 (Claude)...")
    log(f"로그: {output_log}")
    claude_start = time.monotonic()

    # Claude Code 2.1+: --output-format stream-json 은 --verbose 필수 (.sh L111-119).
    # 합성만 성공하면 briefing JSON 이 생기므로, claude 종료코드와 무관하게
    # 이후 briefing 존재 여부로 판단한다 (.sh L112-120).
    # 합성 모델: insight-synthesizer.md frontmatter 의 선언값을 따른다(없으면 sonnet).
    # raw `claude -p` 는 agent frontmatter 를 안 읽으므로 여기서 명시하지 않으면
    # CLI 기본값(Opus)으로 떨어져 ~5배 비싸진다. PRM_SYNTH_MODEL 로 1회 override 가능.
    synth_model = _enforce_cheap_model(
        os.environ.get("PRM_SYNTH_MODEL") or _synth_model_from_spec())
    claude_argv = [
        claude_bin, "-p", prompt,
        "--model", synth_model,
        "--allowedTools", "Read,Write,Edit,Bash",
        "--output-format", "stream-json",
        "--verbose",
    ]
    claude_rc = 0
    try:
        with open(output_log, "w", encoding="utf-8") as logf:
            proc = subprocess.run(claude_argv, stdout=logf,
                                  stderr=subprocess.STDOUT, check=False)
        claude_rc = proc.returncode
    except OSError as e:
        # Spawn itself failed (e.g. binary vanished between which() and run()).
        warn(f"claude 실행 실패 — {e} (로그: {output_log})")
        claude_rc = 1
    if claude_rc != 0:
        warn(f"claude 종료코드 {claude_rc} — briefing 산출 여부로 판단 (로그: {output_log})")

    claude_duration = int(time.monotonic() - claude_start)

    # require_file on briefing — synthesis-failure guard (.sh L125-126).
    briefing_json = paths.PROCESSED_DIR / f"newsletter-briefing-{date}.json"
    try:
        require_file(
            briefing_json,
            f"briefing JSON 미생성 ({briefing_json}) — 합성(Step7) 실패. 로그: {output_log}")
    except SystemExit:
        return _finish(1)

    # ── Step 8~10: 후처리 (HTML 렌더 → PR 누적 → 이메일) ───────── [.sh L128-136] ─
    # 실행 로그는 이 모듈이 전담 — post 자체 기록 비활성 (.sh L131-132).
    # The env flag is the cross-process contract post.run honors when it shells
    # exec-log.py; passed through args too for the in-process call.
    os.environ["PR_MONITOR_EXEC_LOGGED"] = "1"

    from . import post  # lazy: sibling module is a separate port
    post_args = SimpleNamespace(date=date, hours=hours, no_email=no_email,
                                exec_logged=True)
    try:
        post_rc = post.run(post_args)
    except SystemExit as e:
        post_rc = e.code if isinstance(e.code, int) else 1
    if post_rc != 0:
        err("후처리(post) 실패")
        return _finish(1)

    # ── 결과 요약 ────────────────────────────────────────────── [.sh L138-158] ─
    log("=== 실행 완료 ===")
    ok(f"합성 소요: {claude_duration // 60}분 {claude_duration % 60}초")

    # REVIEW_NEEDED 플래그 체크 (.sh L142-145).
    review_needed = paths.OUTPUT_DIR / "REVIEW_NEEDED.md"
    if review_needed.is_file():
        warn(f"수동 검토 필요: {review_needed} 확인")

    # 비용 추출 — stream-json 결과 이벤트에서 JSON 으로 (.sh L147-154; grep/awk 대체).
    cost = _parse_cost(output_log)
    if cost:
        log(f"추정 비용: ${cost:.4f}")

    html_out = paths.NEWSLETTER_OUTPUT_DIR / f"newsletter-report-{date}.html"
    print("")
    print(f"  {html_out}")
    print("")

    return _finish(0)


def _build_args(argv: list[str] | None) -> argparse.Namespace:
    """Standalone arg parsing mirroring the bash flags (.sh L31-43)."""
    p = argparse.ArgumentParser(prog="prmonitor newsletter")
    p.add_argument("date", nargs="?", default=None)
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--no-email", dest="no_email", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(_build_args(None)))
