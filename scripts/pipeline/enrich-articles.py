#!/usr/bin/env python3
"""기사 보강 — Haiku 배치로 기사당 {중요도 1~5, 한국어 1줄요약} 생성.

왜 필요한가: classify.py 의 relevance_score 는 키워드 부스트 기반이라 편집
중요도를 못 잡는다. 화제성 묘기 기사(이색 기록·이벤트성 시연)가 키워드 밀도로 만점을 받고
대형 자금조달·공급계약 같은 실속 뉴스를 tier1 에서 밀어낸다. 키워드로는 묘기 vs 실속 구분이
구조적으로 불가능 — 경쟁사명만 박혀도 tier1 로 자동승격되기 때문.

이 단계가 Haiku 로 각 기사를 읽고 (1) 산업 뉴스레터 독자 기준 중요도, (2) 한국어
1줄요약을 매긴다. aggregate.py 가 이 importance 로 tier 를 정한다(키워드 점수 대신).
ko_summary 는 추출 실패로 비거나 영문인 summary 문제도 함께 해결한다.

실패해도 파이프라인을 막지 않는다 — importance 가 안 붙으면 aggregate 가 기존
relevance_score 로 폴백한다.

입력:  data/processed/classified-{date}.json
출력:  같은 파일에 article["importance"], article["ko_summary"] 추가(in-place)

사용:  python3 scripts/pipeline/enrich-articles.py 2026-06-16
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths, domainpack

ENRICH_MODEL = os.environ.get("PRM_ENRICH_MODEL", "claude-haiku-4-5")


def _industry() -> str:
    """도메인팩에서 산업 분야 — 엔진은 산업을 모른다(콘토소 EV·로봇 등 무관)."""
    try:
        prof = domainpack.load_pack("company-profile")
        ind = (prof.get("company") or {}).get("industry", "")
        if ind:
            return ind
    except Exception:
        pass
    return "해당"


def _build_rubric() -> str:
    """중요도 루브릭 — 도메인 중립. 산업명만 도메인팩에서 주입하고, 등급 정의는
    어느 산업에나 통하는 일반어(자금조달·M&A·규제·노벨티)로 쓴다. 도메인팩이
    importance_hint(선택)를 주면 그 산업 고유의 예시를 덧붙인다."""
    industry = _industry()
    lang = domainpack.get("style", "language", "ko")
    summ_lang = {"ko": "한국어", "en": "English", "ja": "日本語"}.get(lang, lang)
    rubric = f"""중요도(importance) 기준 — {industry} 산업 뉴스레터 임원 독자 관점:
5 = 대형 자금조달·M&A·공급계약·상용 배치·규제·핵심 이해관계자(최대주주·전략 투자자·대형 고객) 행보
4 = 의미있는 제품 출시·전략 파트너십·실적·신규 시장 진입
3 = 일반 업계 동향·연구 발표·전시회 소식
2 = 마이너 업데이트·지역 단신
1 = 노벨티/색다른 화제·이색 기록·묘기성 시연 — 경쟁사명이 박혀 있어도 1
ko_summary = 그 기사가 "무슨 일인지" {summ_lang} 한 문장(40~80자). 제목 번역이 아니라 핵심."""
    hint = domainpack.get("classify-tuning", "importance_hint", "")
    if hint:
        rubric += f"\n\n[이 산업 참고] {hint.strip()}"
    return rubric


def _aid(a: dict) -> str:
    """aggregate.article_id 와 동일 규칙 — url(없으면 title) md5 6자."""
    import hashlib
    key = a.get("url") or a.get("title", "")
    return "a" + hashlib.md5(key.encode("utf-8")).hexdigest()[:6]


def _build_input(articles: list[dict]) -> list[dict]:
    """포함 기사만 {id, title, lead} 로 압축 (토큰 절감)."""
    rows = []
    for a in articles:
        if a.get("decision") == "exclude":
            continue
        lead = (a.get("first_paragraph") or a.get("full_text") or "")[:220]
        rows.append({"id": _aid(a), "title": a.get("title", "")[:120], "lead": lead})
    return rows


def _run_haiku(in_path: Path, out_path: Path) -> bool:
    claude = shutil.which("claude")
    if not claude:
        print("enrich: claude CLI 없음 — 보강 skip (키워드 점수 폴백)")
        return False
    prompt = f"""너는 {_industry()} 산업 뉴스 분류기다. {in_path} 를 읽어라.
각 기사에 대해 중요도(1~5)와 1줄요약을 매긴다.

{_build_rubric()}

{out_path} 에 JSON 배열로 저장한다 — 각 원소는 {{"id": "<입력 id 그대로>", "importance": <1-5 정수>, "ko_summary": "<요약 한 문장>"}}.
입력의 모든 기사를 빠짐없이 포함한다. 설명·잡담 없이 파일만 쓴다."""
    log = paths.LOGS_DIR / f"enrich-{in_path.stem}.log"
    env = dict(os.environ)
    env["MAX_THINKING_TOKENS"] = os.environ.get("PRM_ENRICH_THINKING", "0")
    env.pop("CLAUDE_EFFORT", None)
    argv = [
        claude, "-p", prompt,
        "--model", ENRICH_MODEL,
        "--effort", "low",
        "--allowedTools", "Read,Write",
        "--output-format", "stream-json", "--verbose",
    ]
    try:
        with open(log, "w", encoding="utf-8") as lf:
            subprocess.run(argv, stdout=lf, stderr=subprocess.STDOUT, env=env, check=False)
    except OSError as e:
        print(f"enrich: claude 실행 실패 — {e} (보강 skip)")
        return False
    return out_path.exists()


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else __import__("datetime").date.today().isoformat()
    classified = paths.PROCESSED_DIR / f"classified-{date_str}.json"
    if not classified.exists():
        print(f"ERROR: {classified} 없음. classify.py 먼저 실행.")
        sys.exit(1)

    data = json.loads(classified.read_text(encoding="utf-8"))
    articles = data.get("articles", [])

    # 이미 보강돼 있으면 skip (idempotent — facts 캐시 삭제 후 재집계 시 Haiku 재호출 방지)
    incl = [a for a in articles if a.get("decision") != "exclude"]
    if incl and all("importance" in a for a in incl):
        print(f"enrich: 이미 보강됨 ({len(incl)}건) — skip")
        return

    rows = _build_input(articles)
    if not rows:
        print("enrich: 보강 대상 기사 없음")
        return

    # 출력은 워크스페이스(BRIEFING_DIR) — claude -p 가 .claude/ 캐시 경로를 민감
    # 파일로 분류해 Write 를 차단하기 때문(briefing JSON 과 동일 이유). 입력은
    # Read 만 하므로 캐시(PROCESSED_DIR)에 둬도 무방.
    in_path = paths.PROCESSED_DIR / f"enrich-input-{date_str}.json"
    out_path = paths.BRIEFING_DIR / f"enrich-output-{date_str}.json"
    paths.BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    in_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    if not _run_haiku(in_path, out_path):
        return  # 폴백: aggregate 가 relevance_score 사용

    try:
        enriched = json.loads(out_path.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in enriched if isinstance(e, dict) and "id" in e}
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"enrich: 출력 파싱 실패 — {e} (키워드 점수 폴백)")
        return

    n = 0
    for a in articles:
        e = by_id.get(_aid(a))
        if not e:
            continue
        imp = e.get("importance")
        if isinstance(imp, (int, float)) and 1 <= imp <= 5:
            a["importance"] = int(imp)
        ko = (e.get("ko_summary") or "").strip()
        if ko:
            a["ko_summary"] = ko
        n += 1

    classified.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ enrich: {n}/{len(rows)}건 보강 (importance + ko_summary)")


if __name__ == "__main__":
    main()
