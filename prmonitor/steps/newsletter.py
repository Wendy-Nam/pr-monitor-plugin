"""Newsletter orchestrator — Python port of scripts/newsletter/run-newsletter.sh.

Three-stage pipeline, faithful to the bash ground truth
(ref-pr-monitor/scripts/newsletter/run-newsletter.sh):

    1. pre.run     — URL 수집 → 추출 → 분류 → 집계 → 컨텍스트 (결정론적)
    2. claude -p   — 인사이트 합성 (Step 7, LLM)
    3. post.run    — HTML 렌더 → PR 누적 → 이메일

Faithfully ported behaviors (with .sh line cites inline):
  - Collection window: args.hours > resolve_hours("newsletter")
  - Exec-metric logging on EXIT regardless of status (the bash `trap`)
  - run-pre failure → exit 1
  - --dry-run short-circuit after pre (skip claude)
  - Guard: missing `claude` binary → clear error, exit 1
  - The synthesis prompt heredoc, re-anchored to ABSOLUTE plugin paths
  - claude exit code is informational only — briefing existence decides
  - require_file on the briefing JSON → synthesis-failure guard
  - PR_MONITOR_EXEC_LOGGED=1 so post's own trap stays silent (no dup log)
  - run-post failure → exit 1
  - REVIEW_NEEDED.md notice
  - Cost summary — parsed from the stream-json log via JSON, NOT grep/awk

Deviations from the .sh, mandated by the architecture contract:
  - No bash / os.system / shell=True. Each step is subprocess.run([...]) with an
    explicit argv list, or a direct in-process call to the sibling step module.
  - Paths are 3-root absolute (prmonitor.paths), never `cd`+relative. The prompt's
    spec/input/output references are re-anchored:
      spec    → paths.AGENTS_DIR / "insight-synthesizer.md"   (was .claude/agents/…)
      input   → paths.PROCESSED_DIR / synthesis-context-{date}.json
      output  → paths.BRIEFING_DIR / newsletter-briefing-{date}.json
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

# 합성 모델 정책 — 엔진 설정(도메인 지식 아님). 한 곳에서 바꾼다.
# 기본값은 env PRM_SYNTH_MODEL 또는 agent frontmatter 로 덮을 수 있다.
# 모델 세대가 바뀌면(4-6 → 4-7 …) 이 상수만 고치면 된다.
_SYNTH_MODEL_DEFAULT = os.environ.get("PRM_SYNTH_MODEL_DEFAULT", "claude-sonnet-4-6")
_BLOCKED_MODEL_SUBSTR = "opus"  # 비용 과다 — 합성에 금지

_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
_SAFE_MODEL_RE = __import__("re").compile(r"[a-zA-Z0-9._:/-]{1,80}")


def _safe_model(val: str | None, default: str) -> str:
    """Validate a model name from env — reject values with unexpected chars."""
    val = (val or "").strip()
    if val and _SAFE_MODEL_RE.fullmatch(val):
        return val
    return default


def _safe_effort(val: str | None, default: str) -> str:
    """Validate an effort level from env — allowlist only."""
    val = (val or "").strip().lower()
    return val if val in _VALID_EFFORT else default


def _exec_log(*, date: str, run_id: str, started: str, status: int,
              hours: int, claude_log: str) -> None:
    """Write the execution metric record (CLAUDE.md §6).

    Faithful to the bash `write_exec_log`: invoke
    scripts/lib/exec-log.py with the venv interpreter, recording status/hours,
    the claude stream-json log, and both output artifacts. Never raises — a
    logging failure only warns, exactly like the bash `|| warn`.
    """
    briefing = paths.BRIEFING_DIR / f"newsletter-briefing-{date}.json"
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
    val = _SYNTH_MODEL_DEFAULT
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
    if _BLOCKED_MODEL_SUBSTR in (model or "").lower():
        warn(f"합성 모델 '{model}' → 비용 보호로 {_SYNTH_MODEL_DEFAULT} 강등")
        return _SYNTH_MODEL_DEFAULT
    return model or _SYNTH_MODEL_DEFAULT


def _synth_prompt(date: str) -> str:
    """The Step-7 synthesis prompt — ported from the heredoc.

    Same instructions, but every relative path is re-anchored to an absolute
    3-root path so the headless `claude` session resolves files regardless of
    its cwd (the plugin model has no single flat root to `cd` into).
    """
    spec = paths.AGENTS_DIR / "insight-synthesizer.md"
    synthesis_ctx = paths.PROCESSED_DIR / f"synthesis-context-{date}.json"
    briefing = paths.BRIEFING_DIR / f"newsletter-briefing-{date}.json"
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


def _core_prompt(date: str, core_out) -> str:
    """병렬 경로 '교차' 호출 — tldr·insights·glossary·landscape 만 생성(동향·헤드라인 제외)."""
    spec = paths.AGENTS_DIR / "insight-synthesizer.md"
    synthesis_ctx = paths.PROCESSED_DIR / f"synthesis-context-{date}.json"
    org = domainpack.get("branding", "org_name", "")
    return f"""너는 {org} 뉴스레터 합성의 '교차' 세션이다. {spec} 의 규칙(톤·인사이트·자가검증)을 따른다.
입력 {synthesis_ctx} 하나만 읽는다 (tier1 팩트 + tier2 + 자사 맥락 인라인).
**이번 호출은 tldr · insights · landscape_update_points 만 생성한다.**
category_summary · headlines · company_glossary 는 다른 단계가 담당하니 **만들지 마라.**
각 insight 의 observation·implication 은 **문장당 60자 이내**로 쪼갠다 ("A이고 B이며 C다" → "A다. B다. C다."). 긴 복문 금지.
{core_out} 에 JSON 으로 저장: {{"tldr": "...", "insights": [...], "landscape_update_points": [...]}}.
설명·잡담 없이 파일만 쓴다. Task/Agent 서브에이전트 스폰 금지."""


def _cat_prompt(date: str, cid: str, slice_path, out_path) -> str:
    """병렬 경로 단일 카테고리 동향 호출."""
    spec = paths.AGENTS_DIR / "category-digest.md"
    return f"""{spec} 의 규칙을 따르는 단일 카테고리 동향 합성 세션이다 (category_id={cid}).
입력 {slice_path} 하나만 읽는다.
{out_path} 에 스펙 스키마({{"category_id","category_name","summary","headlines"}})로 저장한다.
설명·잡담 없이 파일만 쓴다. Task/Agent 서브에이전트 스폰 금지."""


def _glossary_prompt(date, briefing_path, ctx_path, gloss_out) -> str:
    """post-merge 용어집 — 최종 원고에 실제 등장한 비유명 회사만 스캔(경제적·누락0)."""
    return f"""너는 뉴스레터 '이번 호 등장 기업' 용어집 보강 세션이다.
{briefing_path} (최종 원고: tldr·insights·category_summary·headlines)를 처음부터 끝까지 읽고,
원고에 **실제로 등장하는** 회사 중 독자가 처음 들을 만한 **비유명** 회사에 1줄 한국어 설명을 붙인다.
- 제외: {ctx_path} 의 self_context_bundle.competitors_yaml 의 경쟁사, 빅테크(NVIDIA·Amazon·Google·Microsoft·Samsung 등), 자사, 이미 유명한 곳.
- 설명 = **그 회사가 평소 "무엇을 하는 곳인지" 일반 개요**(30~70자). "[국가] [주력 분야] [업태]"를 기본으로, 회사 배경(대표 제품·설립연도·규모·소속/모회사)이 facts 에 있으면 한 절 덧붙인다. 독자가 본문 읽다 "이 회사 뭐지?" 할 때 정체를 알려주는 **사전 항목**이다.
  - ⛔ **이번 호 뉴스·사건(투자·수주·MoU·발표·시연 등)을 설명에 넣지 마라** — 그건 본문/인사이트가 이미 다루므로 중복이다. 글로서리는 회사 자체 개요만.
  - 회사 배경이 안 보이면 식별 가능한 개요(국가·분야·업태)만 짧게. 뉴스로 분량을 채우지 마라.
  - 과장·평가·전망 금지. 영문 회사명은 원문 그대로(음역 금지).
  - ✅ "[국가] [주력 분야] 개발사. [대표 제품·기술]을 만든다." (정체성 + 평소 배경)
  - ❌ "[국가] [분야] 개발사. [이번 호 투자·수주·발표 등 사건]." (← 이번 호 뉴스는 본문과 중복 — 넣지 마라)
- **원고에 안 나온 회사는 넣지 마라(경제성). 원고에 나온 비유명 회사는 빠짐없이(누락 0).**
{gloss_out} 에 JSON 배열로 저장: [{{"name":"...","desc":"..."}}]. 설명·잡담 없이 파일만 쓴다."""


def _run_parallel_synth(date, claude_bin, synth_model, synth_effort, synth_env):
    """교차 호출 1개 + 카테고리별 호출 N개를 동시 실행하고 briefing 으로 머지한다.

    카테고리 동향이 출력의 대부분이므로 카테고리별로 쪼개 병렬화하면 wall-clock 이
    sum→max 로 준다. 각 호출은 자기 슬라이스만 보므로 입력도 작고 집중돼 품질도 오른다.
    """
    import json
    from concurrent.futures import ThreadPoolExecutor

    ctx_path = paths.PROCESSED_DIR / f"synthesis-context-{date}.json"
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    cats = ctx.get("categories", []) or []
    self_full = ctx.get("self_context_bundle", {}) or {}
    # 동향은 외부 서술 — 기준선·톤만 필요(narrative/key-events 는 인사이트용이라 제외 = 입력 축소)
    self_slim = {k: self_full[k] for k in ("competitor_landscape", "style_rules")
                 if self_full.get(k)}
    t2_by_cat: dict = {}
    for h in ctx.get("tier2_headlines", []) or []:
        t2_by_cat.setdefault(h.get("category", ""), []).append(h)

    paths.BRIEFING_DIR.mkdir(parents=True, exist_ok=True)  # claude 가 mkdir 안 하도록 선생성
    jobs = []  # (label, out_path, prompt)
    # LLM 출력은 워크스페이스(BRIEFING_DIR)로 — claude -p 는 .claude/ 캐시 경로 Write 를
    # '민감 파일'로 차단한다. 슬라이스 입력(synctx-cat)은 Python 이 캐시에 써도 Read 는 허용.
    core_out = paths.BRIEFING_DIR / f"briefing-core-{date}.json"
    jobs.append(("core", core_out, _core_prompt(date, core_out)))
    # 카테고리 이름 맵 (tier2-only 카테고리용)
    try:
        _cat_defs = domainpack.load_pack("categories").get("categories", {}) or {}
    except Exception:  # noqa: BLE001
        _cat_defs = {}
    tier1_by_cid = {c.get("category_id", ""): c for c in cats if c.get("category_id")}
    # tier1 + tier2-only 모든 카테고리에 digest 생성 — tier2-only(예: funding) 누락 방지
    all_cids = list(tier1_by_cid) + [c for c in t2_by_cat if c and c not in tier1_by_cid]
    cat_outs = []  # (cid, out, name)
    for cid in all_cids:
        c = tier1_by_cid.get(cid, {})
        name = c.get("category_name") or (_cat_defs.get(cid) or {}).get("label_ko", cid)
        sl = {"date": date, "category_id": cid, "category_name": name,
              "facts": c.get("facts", []),
              "tier2_headlines": t2_by_cat.get(cid, []),
              "self_context": self_slim}
        sl_path = paths.PROCESSED_DIR / f"synctx-cat-{cid}-{date}.json"
        sl_path.write_text(json.dumps(sl, ensure_ascii=False, indent=2), encoding="utf-8")
        out = paths.BRIEFING_DIR / f"briefing-cat-{cid}-{date}.json"
        cat_outs.append((cid, out, name))
        jobs.append((f"cat-{cid}", out, _cat_prompt(date, cid, sl_path, out)))

    def _run_one(job):
        label, out, prompt = job
        logp = paths.LOGS_DIR / f"synth-{label}-{date}.log"
        argv = [claude_bin, "-p", prompt, "--model", synth_model,
                "--effort", synth_effort, "--allowedTools", "Read,Write,Edit",
                "--add-dir", str(paths.PROJECT_DIR),  # 워크스페이스 출력 Write 허용
                "--output-format", "stream-json", "--verbose"]
        # 일시 실패(API 529 Overloaded 등)에 재시도 — 실패하면 그 카테고리가 통째로
        # 드롭되므로 백오프 재시도로 방어한다.
        for attempt in range(3):
            try:
                out.unlink()
            except FileNotFoundError:
                pass
            try:
                with open(logp, "w", encoding="utf-8") as lf:
                    subprocess.run(argv, stdout=lf, stderr=subprocess.STDOUT,
                                   env=synth_env, check=False)
            except OSError as e:
                warn(f"합성 호출 실패 [{label}] — {e}")
            if out.exists():
                return label, True
            if attempt < 2:
                time.sleep(3 * (attempt + 1))  # 529 는 일시적 — 짧게 재시도
        warn(f"합성 재시도 후에도 실패 [{label}] — 해당 항목 누락 가능")
        return label, False

    # 동시 6 (속도 우선). 529 Overloaded 는 _run_one 의 빠른 재시도가 흡수 — 동시성을
    # 낮춰 웨이브를 늘리는 것보다, 높게 유지하고 일시 실패만 재시도하는 게 빠르고 안정적.
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_run_one, jobs))

    # 병렬 배치에서 실패한 호출은 순차 재시도 — 동시 부하가 사라진 상태라 529 Overloaded 가
    # 거의 사라진다. (병렬 중엔 다른 호출이 계속 API 를 때려 재시도도 529 나던 문제 차단.)
    for job in jobs:
        if not job[1].exists():
            log(f"  순차 재시도 [{job[0]}] (병렬 실패분 — 부하 없이)")
            _run_one(job)

    # 머지
    briefing = {"date": date, "tldr": "", "insights": [], "company_glossary": [],
                "landscape_update_points": [], "category_summary": [], "headlines": []}
    if core_out.exists():
        try:
            core = json.loads(core_out.read_text(encoding="utf-8"))
            briefing["tldr"] = core.get("tldr", "")
            briefing["insights"] = core.get("insights", [])
            briefing["company_glossary"] = core.get("company_glossary", []) or core.get("glossary", [])
            briefing["landscape_update_points"] = core.get("landscape_update_points", [])
        except (json.JSONDecodeError, OSError) as e:
            warn(f"core 머지 실패 — {e}")
    for cid, out, cname in cat_outs:
        if not out.exists():
            warn(f"카테고리 동향 누락 [{cid}]")
            continue
        try:
            d = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            warn(f"카테고리 머지 실패 [{cid}] — {e}")
            continue
        briefing["category_summary"].append({
            "category_id": cid,
            "category_name": d.get("category_name", cname),
            "summary": d.get("summary", "")})
        for h in d.get("headlines", []) or []:
            h.setdefault("group", cid)
            briefing["headlines"].append(h)

    (paths.BRIEFING_DIR / f"newsletter-briefing-{date}.json").write_text(
        json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"병렬 합성 머지: core={'OK' if core_out.exists() else 'FAIL'}, "
        f"카테고리 {len(briefing['category_summary'])}/{len(cat_outs)}, "
        f"헤드라인 {len(briefing['headlines'])}건")

    # 용어집(company_glossary)은 최종 원고 기준 보강 — 렌더 원고에 실제 등장한 비유명
    # 회사만(경제적·누락 0). Haiku 1회. (core 가 입력 전체를 추측 스캔하면 과다+누락 둘 다 남음)
    briefing_path = paths.BRIEFING_DIR / f"newsletter-briefing-{date}.json"
    gloss_out = paths.BRIEFING_DIR / f"briefing-glossary-{date}.json"
    try:
        gloss_out.unlink()
    except FileNotFoundError:
        pass
    gargv = [claude_bin, "-p", _glossary_prompt(date, briefing_path, ctx_path, gloss_out),
             "--model", _safe_model(os.environ.get("PRM_GLOSSARY_MODEL"), "claude-sonnet-4-6"),
             "--effort", "low", "--allowedTools", "Read,Write",
             "--add-dir", str(paths.PROJECT_DIR),
             "--output-format", "stream-json", "--verbose"]
    for attempt in range(2):  # 529 등 일시 실패 1회 재시도
        try:
            with open(paths.LOGS_DIR / f"synth-glossary-{date}.log", "w", encoding="utf-8") as lf:
                subprocess.run(gargv, stdout=lf, stderr=subprocess.STDOUT, env=synth_env, check=False)
            if gloss_out.exists():
                gl = json.loads(gloss_out.read_text(encoding="utf-8"))
                if isinstance(gl, list) and gl:
                    briefing["company_glossary"] = gl
                    briefing_path.write_text(
                        json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
                    log(f"용어집 보강: {len(gl)}곳")
                break
        except (OSError, json.JSONDecodeError) as e:
            warn(f"용어집 보강 실패 — {e}")
        if attempt < 1:
            time.sleep(5)

    # 임시 분할 산출물 정리 (머지 완료 — 디버깅 필요 시 PRM_KEEP_SPLITS 로 보존)
    if not os.environ.get("PRM_KEEP_SPLITS"):
        temps = [core_out, gloss_out] + [o for _, o, _ in cat_outs]
        temps += [paths.PROCESSED_DIR / f"synctx-cat-{c.get('category_id','')}-{date}.json"
                  for c in cats if c.get("category_id")]
        for p in temps:
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def _parse_cost(claude_log: str):
    """Total cost from the stream-json log, via JSON — not grep/awk.

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
    # 시간창: 인자 > pipelines.yaml (월요일 보정 포함)
    hours = getattr(args, "hours", None)
    if hours is None:
        hours = resolve_hours("newsletter")
    hours = int(hours)
    dry_run = bool(getattr(args, "dry_run", False))
    no_email = bool(getattr(args, "no_email", False))

    # Writable dir skeleton (mkdir -p data/raw data/processed …)
    paths.ensure_dirs()

    # ── exec-log scaffolding (the bash EXIT trap) ──────────────────
    # stream-json log path mirrors logs/executions/newsletter-{F-HM}.log
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    output_log = str(paths.LOGS_DIR / f"newsletter-{timestamp}.log")
    run_id = datetime.now().strftime("%H%M%S")
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    # status carries the EXIT code passed to the trap; updated as we go so the
    # log records the real outcome even on an early failure (.sh `trap ... EXIT`).
    status = 0

    def _finish(code: int) -> int:
        # Equivalent of `trap 'write_exec_log $?' EXIT`: always log.
        _exec_log(date=date, run_id=run_id, started=started_at,
                  status=code, hours=hours, claude_log=output_log)
        return code

    log(f"=== 뉴스레터 실행 ({date}, {hours}h) ===")

    # ── Step 1~6: 전처리 (결정론적) — pre.run ─
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
        log("DRY RUN — Claude 호출 skip (전처리 완료)")          #
        print(f"  data/processed/newsletter-facts-{date}.json")
        return _finish(0)

    # ── Step 7: 인사이트 합성 (Claude) ─────────────────────────── ─
    # Guard missing claude binary with a clear error.
    claude_bin = shutil.which("claude")
    if not claude_bin:
        err("claude CLI 없음. https://docs.claude.com 참고해 Claude Code 설치.")
        return _finish(1)

    prompt = _synth_prompt(date)

    log("Step 7: 인사이트 합성 시작 (Claude)...")
    log(f"로그: {output_log}")
    claude_start = time.monotonic()

    # Claude Code 2.1+: --output-format stream-json 은 --verbose 필수.
    # 합성만 성공하면 briefing JSON 이 생기므로, claude 종료코드와 무관하게
    # 이후 briefing 존재 여부로 판단한다.
    # 합성 모델: insight-synthesizer.md frontmatter 의 선언값을 따른다(없으면 sonnet).
    # raw `claude -p` 는 agent frontmatter 를 안 읽으므로 여기서 명시하지 않으면
    # CLI 기본값(Opus)으로 떨어져 ~5배 비싸진다. PRM_SYNTH_MODEL 로 1회 override 가능.
    synth_model = _enforce_cheap_model(
        _safe_model(os.environ.get("PRM_SYNTH_MODEL"), _synth_model_from_spec()))
    # agent 모드 기본 extended thinking 이 30분 폭주를 일으킴 — effort 로 캡한다.
    # 입력(synthesis-context)이 작아 low 로도 Sonnet 교차합성 품질은 유지된다.
    synth_effort = _safe_effort(os.environ.get("PRM_SYNTH_EFFORT"), "medium")
    claude_argv = [
        claude_bin, "-p", prompt,
        "--model", synth_model,
        "--effort", synth_effort,
        "--allowedTools", "Read,Write,Edit,Bash",
        "--add-dir", str(paths.PROJECT_DIR),  # 워크스페이스 briefing Write 허용
        "--output-format", "stream-json",
        "--verbose",
    ]
    # 진짜 병목은 headless extended thinking — rate-limit 시 6900토큰이 9분으로 불어난다.
    # 합성은 추론이 아니라 스펙대로의 구조적 생성이라 thinking 이 품질을 못 사준다.
    # MAX_THINKING_TOKENS=0 으로 완전 비활성(중간값은 --effort 에 밀려 무시됨). 품질은
    # 프롬프트·self-context 데이터로 잡는다. PRM_SYNTH_THINKING 로 1회 override 가능.
    # 부모 세션의 CLAUDE_EFFORT(=high 등)가 새지 않도록 서브프로세스 env 를 명시 구성.
    synth_env = dict(os.environ)
    synth_env["MAX_THINKING_TOKENS"] = os.environ.get("PRM_SYNTH_THINKING", "0")
    synth_env.pop("CLAUDE_EFFORT", None)  # --effort 플래그가 정본
    # stale-briefing 가드: 합성 직전 기존 briefing 삭제 → 아래 require_file 의 "존재"가
    # "이번 합성이 실제로 산출함"을 의미하게 한다. (claude -p 가 새 briefing 을 못 쓰면
    # 옛 briefing 이 조용히 렌더되던 버그 차단 — 실패 시 stale 대신 명확히 중단.)
    briefing_json = paths.BRIEFING_DIR / f"newsletter-briefing-{date}.json"
    briefing_json.unlink(missing_ok=True)
    claude_rc = 0
    if os.environ.get("PRM_SYNTH_PARALLEL", "1") != "0":  # 기본 병렬, =0 이면 단일 폴백
        # 병렬 분리: 교차 호출 1 + 카테고리별 N 동시 → briefing 머지(아래 require_file 가 판정).
        log("Step 7: 병렬 분리 합성 (교차 1 + 카테고리별 N, 동시 실행)...")
        try:
            _run_parallel_synth(date, claude_bin, synth_model, synth_effort, synth_env)
        except Exception as e:  # noqa: BLE001 — briefing 존재 여부로 최종 판단
            warn(f"병렬 합성 예외 — {e} (briefing 산출 여부로 판단)")
    else:
      try:
        with open(output_log, "w", encoding="utf-8") as logf:
            proc = subprocess.run(claude_argv, stdout=logf, env=synth_env,
                                  stderr=subprocess.STDOUT, check=False)
        claude_rc = proc.returncode
      except OSError as e:
        # Spawn itself failed (e.g. binary vanished between which() and run()).
        warn(f"claude 실행 실패 — {e} (로그: {output_log})")
        claude_rc = 1
    if claude_rc != 0:
        warn(f"claude 종료코드 {claude_rc} — briefing 산출 여부로 판단 (로그: {output_log})")

    claude_duration = int(time.monotonic() - claude_start)

    # require_file on briefing — synthesis-failure guard.
    briefing_json = paths.BRIEFING_DIR / f"newsletter-briefing-{date}.json"
    try:
        require_file(
            briefing_json,
            f"briefing JSON 미생성 ({briefing_json}) — 합성(Step7) 실패. 로그: {output_log}")
    except SystemExit:
        return _finish(1)

    # ── Step 8~10: 후처리 (HTML 렌더 → PR 누적 → 이메일) ───────── ─
    # 실행 로그는 이 모듈이 전담 — post 자체 기록 비활성.
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

    # ── 결과 요약 ────────────────────────────────────────────── ─
    log("=== 실행 완료 ===")
    ok(f"합성 소요: {claude_duration // 60}분 {claude_duration % 60}초")

    # REVIEW_NEEDED 플래그 체크.
    review_needed = paths.OUTPUT_DIR / "REVIEW_NEEDED.md"
    if review_needed.is_file():
        warn(f"수동 검토 필요: {review_needed} 확인")

    # 비용 추출 — stream-json 결과 이벤트에서 JSON 으로 (; grep/awk 대체).
    cost = _parse_cost(output_log)
    if cost:
        log(f"추정 비용: ${cost:.4f}")

    html_out = paths.NEWSLETTER_OUTPUT_DIR / f"newsletter-report-{date}.html"
    print("")
    print(f"  {html_out}")
    print("")

    return _finish(0)


def _build_args(argv: list[str] | None) -> argparse.Namespace:
    """Standalone arg parsing mirroring the bash flags."""
    p = argparse.ArgumentParser(prog="prmonitor newsletter")
    p.add_argument("date", nargs="?", default=None)
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--no-email", dest="no_email", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(_build_args(None)))
