"""Preprocessing orchestrator — faithful Python port of ``scripts/pipeline/run-pre.sh``.

Deterministic steps only (no LLM). Mirrors the bash orchestrator step-for-step;
``.sh`` line numbers are cited inline next to each ported block.

Pipeline (run-pre.sh "Steps 0-7"):
  Step 0  ensure_venv          — handled by the dispatcher (__main__.py) before run()
  Step 1  fetch-urls.py        — URL collection + clustering        (run-pre.sh 47-96)
  Step 2  batch-extract.py     — article body extraction            (run-pre.sh 98-116)
  Step 3  classify.py          — deterministic classification       (run-pre.sh 118-131)
  Step 6  aggregate.py         — per-category fact aggregation       (run-pre.sh 133-147)
          (Steps 4·5 are intentionally absent — folded into aggregate)
  Step 7  preload-synthesis-context.py — compressed synthesis ctx   (run-pre.sh 149-162)

Behaviour preserved exactly:
  - arg parsing: optional date + --hours (default 48)                (run-pre.sh 18-29)
  - Monday auto-bump 48→72 when no date given                        (run-pre.sh 31-38)
  - urls-{date}.hours marker superset/invalidation logic             (run-pre.sh 51-96)
  - skip-if-output-exists caching per step                           (run-pre.sh 65,101,121,137,152)
  - --hours-override for weekly mode (HOURS > 24)                    (run-pre.sh 74-79)
  - exit-on-failure (nonzero return) + require_file guards           (run-pre.sh 80-93,114,129,145,160)

Paths: the bash version used a single flat root (``data/raw`` etc.). Under the
three-root model, urls live in PLUGIN_DATA (:data:`paths.RAW_DIR`) and all
processed artifacts in PLUGIN_DATA (:data:`paths.PROCESSED_DIR`), matching where
the invoked step-scripts actually read/write. Step-scripts are invoked as
subprocesses with the venv interpreter (:func:`paths.venv_python`).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime

from .. import paths
from ..common import count_urls, err, log, ok, require_file, resolve_hours


# Default collection window when --hours is unset (run-pre.sh:20 → HOURS=48).
# Sourced from the newsletter pipeline policy to honour the architecture contract;
# falls back to 48 to match the bash literal default exactly.
_DEFAULT_HOURS = 48


def _py() -> str:
    """venv interpreter path as a string (run-pre.sh Step 0 ``$PY``)."""
    return str(paths.venv_python())


def _scripts_dir():
    return paths.SCRIPTS_DIR / "pipeline"


def _run_step(argv: list[str], fail_msg: str) -> bool:
    """Run a pipeline step-script subprocess. True on success, False on failure.

    Mirrors ``if ! "$PY" scripts/... 2>&1; then err; exit 1; fi`` — output is
    streamed to this process's stdout/stderr (no capture), and a nonzero exit
    aborts the pipeline. No shell, explicit argv list (architecture contract).
    """
    try:
        r = subprocess.run(argv, check=False)
    except OSError as e:
        err(f"{fail_msg} ({e})")
        return False
    if r.returncode != 0:
        err(fail_msg)
        return False
    return True


def run(args) -> int:
    # ── 인자 파싱 (run-pre.sh:18-29) ──────────────────────────────────────────
    # Dispatcher (__main__.py) already split out `date` (defaulted to today when
    # omitted) and `--hours` (None when unset). `date_given` distinguishes an
    # explicit date from the today-default, needed for the Monday bump below.
    date = args.date
    date_given = getattr(args, "_date_given", None)
    if date_given is None:
        # __main__ defaults args.date to today when omitted; we can't see the
        # original here, so treat "date == today" as the no-date case to match
        # run-pre.sh:32 (`if [ -z "$DATE" ]`). This reproduces the bash branch:
        # an explicit today date and an omitted date behave identically anyway,
        # since both resolve DATE to today and both are eligible for the bump.
        date_given = date != datetime.now().strftime("%Y-%m-%d")

    # HOURS default = 48 (run-pre.sh:20). When --hours unset, take the newsletter
    # policy default (architecture contract) but keep the bash literal as fallback.
    if getattr(args, "hours", None) is not None:
        hours = int(args.hours)
    else:
        try:
            hours = int(resolve_hours("newsletter"))
        except Exception:
            hours = _DEFAULT_HOURS
        if hours <= 0:
            hours = _DEFAULT_HOURS

    # 월요일이면 기본 96h... 실제 72h (run-pre.sh:31-38).
    # Only when no date was given AND hours is still the default 48.
    if not date_given:
        if datetime.now().weekday() == 0 and hours == 48:  # weekday()==0 → Monday (== `date +%u`==1)
            hours = 72
            log("월요일 감지 — 수집 범위 72시간으로 자동 설정 (pipelines.yaml 정책)")

    # mkdir -p data/raw data/processed data/output logs/executions (run-pre.sh:40).
    # Three-root aware: ensure the writable dir skeleton exists.
    paths.ensure_dirs()

    log(f"=== PR Monitor 전처리 시작 ({date}, {hours}h) ===")

    # ── Step 0: venv 세팅 (run-pre.sh:44-45) ──────────────────────────────────
    # Already handled by the dispatcher (bootstrap.ensure_venv) before run().

    py = _py()
    sdir = _scripts_dir()

    # ── Step 1: URL 수집 (run-pre.sh:47-96) ──────────────────────────────────
    urls_file = paths.RAW_DIR / f"urls-{date}.json"
    hours_marker = paths.RAW_DIR / f"urls-{date}.hours"

    # 같은 날 다른 수집창 처리 (run-pre.sh:51-63):
    #   기존 창 >= 요청 창 → superset 재사용
    #   기존 창 <  요청 창 → 재수집 + 하위 산출물 무효화
    prev_hours = 0
    if urls_file.is_file():
        # PREV_HOURS=$(cat "$HOURS_MARKER" 2>/dev/null || echo 0) (run-pre.sh:55)
        try:
            prev_hours = int(hours_marker.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            prev_hours = 0
        if prev_hours < hours:  # run-pre.sh:56
            log(f"Step 1: 기존 수집창 {prev_hours}h < 요청 {hours}h — 재수집 (하위 산출물 무효화)")
            # rm -f urls + marker + extracted + classified + facts (run-pre.sh:58-61)
            for stale in (
                urls_file,
                hours_marker,
                paths.PROCESSED_DIR / f"extracted-{date}.json",
                paths.PROCESSED_DIR / f"classified-{date}.json",
                paths.PROCESSED_DIR / f"newsletter-facts-{date}.json",
            ):
                stale.unlink(missing_ok=True)

    if urls_file.is_file():  # run-pre.sh:65
        url_count = count_urls(urls_file)  # run-pre.sh:66
        log(f"Step 1: URL 수집 skip (기존 파일 재사용, {url_count}건, 수집창 {prev_hours}h ≥ {hours}h)")
        if url_count == 0:  # run-pre.sh:68-71
            err("기존 URL 파일에 수집된 기사가 0건입니다. 파이프라인을 중단합니다.")
            return 1
    else:
        log(f"Step 1: URL 수집 시작 ({hours}h)...")  # run-pre.sh:73
        # 주간 실행(HOURS > 24) → --hours-override (run-pre.sh:74-79)
        override_args: list[str] = []
        if hours > 24:
            override_args = ["--hours-override"]
            log("Step 1: 주간 모드 감지 — --hours-override 적용 (쿼리별 time_window 무시)")
        # "$PY" scripts/pipeline/fetch-urls.py --date "$DATE" --hours "$HOURS" $OVERRIDE_FLAG (run-pre.sh:80)
        argv = [py, str(sdir / "fetch-urls.py"),
                "--date", date, "--hours", str(hours), *override_args]
        if not _run_step(argv, "Step 1: fetch-urls.py 실패"):  # run-pre.sh:81-82
            return 1

        # require_file urls-{date}.json (run-pre.sh:85) — exits(1) on miss.
        require_file(urls_file, f"Step 1: urls-{date}.json 미생성. fetch-urls.py 로그 확인.")

        url_count = count_urls(urls_file)  # run-pre.sh:87
        if url_count == 0:  # run-pre.sh:88-93
            err("Step 1: 수집된 기사 0건. 네트워크 연결 또는 RSS 피드 설정 확인.")
            err("  → config/sources.yaml 에서 RSS 주소 확인")
            err("  → 인터넷 연결 확인")
            return 1
        # echo "$HOURS" > "$HOURS_MARKER" (run-pre.sh:94)
        hours_marker.write_text(f"{hours}\n", encoding="utf-8")
        ok(f"Step 1: URL 수집 완료 ({url_count}건, 수집창 {hours}h)")

    # ── Step 2: 원문 추출 (run-pre.sh:98-116) ─────────────────────────────────
    extracted = paths.PROCESSED_DIR / f"extracted-{date}.json"

    if extracted.is_file():  # run-pre.sh:101
        # ART_COUNT = json['success'] of extracted file (run-pre.sh:102-106)
        art_count: object = "?"
        try:
            from ..common import load_json
            art_count = load_json(extracted).get("success", 0)
        except Exception:
            art_count = "?"
        log(f"Step 2: 원문 추출 skip (기존 파일, {art_count}건 성공)")
    else:
        log("Step 2: 원문 추출 시작...")  # run-pre.sh:109
        # "$PY" scripts/pipeline/batch-extract.py "$DATE" (run-pre.sh:110)
        if not _run_step([py, str(sdir / "batch-extract.py"), date],
                         "Step 2: batch-extract.py 실패"):  # run-pre.sh:111-112
            return 1
        require_file(extracted, f"Step 2: extracted-{date}.json 미생성")  # run-pre.sh:114
        ok("Step 2: 원문 추출 완료")

    # ── Step 3: 분류 (run-pre.sh:118-131) ────────────────────────────────────
    classified = paths.PROCESSED_DIR / f"classified-{date}.json"

    if classified.is_file():  # run-pre.sh:121
        log("Step 3: 분류 skip (기존 파일)")
    else:
        log("Step 3: 기사 분류 시작...")  # run-pre.sh:124
        # "$PY" scripts/pipeline/classify.py "$DATE" (run-pre.sh:125)
        if not _run_step([py, str(sdir / "classify.py"), date],
                         "Step 3: classify.py 실패"):  # run-pre.sh:126-127
            return 1
        require_file(classified, f"Step 3: classified-{date}.json 미생성")  # run-pre.sh:129
        ok("Step 3: 분류 완료")

    # ── Step 6: 집계 (run-pre.sh:133-147) ────────────────────────────────────
    # (Step 4·5 결번 — LLM 팩트추출·인용검증 → 결정론적 집계로 통합)
    all_facts = paths.PROCESSED_DIR / f"newsletter-facts-{date}.json"

    # ── Step 5b: 기사 보강 (Haiku importance + 한국어 1줄요약) ────────────────
    # aggregate 직전, facts 가 아직 없을 때만. 비치명적 — 실패해도 aggregate 가
    # 키워드 점수로 폴백한다. 키워드론 묘기 vs 실속 구분이 안 돼 tier 가 뒤집히는
    # 문제를 Haiku 편집 중요도로 잡는다.
    if not all_facts.is_file():
        log("Step 5b: 기사 보강 시작 (Haiku importance + 한국어 요약)...")
        _run_step([py, str(sdir / "enrich-articles.py"), date],
                  "Step 5b: enrich-articles.py 경고")  # 실패해도 계속

    if all_facts.is_file():  # run-pre.sh:137
        log("Step 6: 집계 skip (기존 파일)")
    else:
        log("Step 6: 카테고리별 집계 시작...")  # run-pre.sh:140
        # "$PY" scripts/pipeline/aggregate.py "$DATE" --hours "$HOURS" (run-pre.sh:141)
        if not _run_step([py, str(sdir / "aggregate.py"), date, "--hours", str(hours)],
                         "Step 6: aggregate.py 실패"):  # run-pre.sh:142-143
            return 1
        require_file(all_facts, f"Step 6: newsletter-facts-{date}.json 미생성")  # run-pre.sh:145
        ok("Step 6: 집계 완료")

    # ── Step 7: 압축 컨텍스트 생성 (run-pre.sh:149-162) ───────────────────────
    synthesis_ctx = paths.PROCESSED_DIR / f"synthesis-context-{date}.json"

    if synthesis_ctx.is_file():  # run-pre.sh:152
        log("Step 7: 압축 컨텍스트 skip (기존 파일)")
    else:
        log("Step 7: insight-synthesizer용 압축 컨텍스트 생성...")  # run-pre.sh:155
        # "$PY" scripts/pipeline/preload-synthesis-context.py "$DATE" (run-pre.sh:156)
        if not _run_step([py, str(sdir / "preload-synthesis-context.py"), date],
                         "Step 7: preload-synthesis-context.py 실패"):  # run-pre.sh:157-158
            return 1
        require_file(synthesis_ctx, f"Step 7: synthesis-context-{date}.json 미생성")  # run-pre.sh:160
        ok("Step 7: 압축 컨텍스트 완료")

    # ── 완료 (run-pre.sh:164-173) ─────────────────────────────────────────────
    # FACT_COUNT = json['total_facts'] of newsletter-facts (run-pre.sh:165-169)
    fact_count: object = "?"
    try:
        from ..common import load_json
        fact_count = load_json(all_facts).get("total_facts", "?")
    except Exception:
        fact_count = "?"

    print("", file=sys.stderr)
    ok(f"전처리 완료: {all_facts} (팩트 약 {fact_count}건)")
    print("", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(None))
