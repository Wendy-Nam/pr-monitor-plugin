#!/usr/bin/env python3
"""Step 6: 카테고리별 팩트 집계 (LLM 호출 없음)

입력: data/processed/classified-{date}.json
설정: config/company-profile.yaml
출력: data/processed/newsletter-facts-{date}.json

classified → 카테고리별 그룹핑 + 중복 제거 + 구조화.
insight-synthesizer 가 이 파일을 입력으로 사용.
"""
from __future__ import annotations  # ponytail: PEP 604 unions on py3.9 venv

import hashlib
import sys
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ (lib import)
from lib.common import CONFIG_DIR, PROCESSED_DIR, load_json, load_yaml, save_json

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import domainpack


def load_company_profile() -> dict:
    return load_yaml(CONFIG_DIR / "company-profile.yaml")


# ── 중복 제거 ────────────────────────────────────────────────
def title_similarity(a: str, b: str) -> float:
    """두 제목의 유사도 (0~1)"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


TIER1_SCORE_THRESHOLD = 4   # relevance_score >= 4 → 주요소식 후보
TIER1_CAT_MAX = 5           # 카테고리당 tier1 최대 건수


def recency_bonus(article: dict, ref_date: str) -> float:
    """최근 기사일수록 보너스. 2일 이내 +1.0, 4일 이내 +0.5, 그 외 0."""
    pub = article.get("published_date", "") or article.get("date", "")
    if not pub:
        return 0.0
    try:
        ref = datetime.strptime(ref_date, "%Y-%m-%d")
        art = datetime.strptime(pub[:10], "%Y-%m-%d")
        delta = (ref - art).days
        if delta <= 2:
            return 1.0
        if delta <= 4:
            return 0.5
    except ValueError:
        pass
    return 0.0


def sort_key(article: dict, ref_date: str) -> float:
    return article.get("relevance_score", 0) + recency_bonus(article, ref_date)


def dedup_articles(articles: list[dict], threshold: float = 0.7) -> list[dict]:
    """제목 유사도 기반 중복 제거. 먼저 온 기사(score 높은 순 정렬 후) 유지."""
    kept = []
    for art in articles:
        title = art.get("title", "")
        is_dup = False
        for existing in kept:
            if title_similarity(title, existing.get("title", "")) > threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(art)
    return kept


# ── 저신호 기사 필터 (tier1 제외 대상) ──────────────────────
_classify_tuning = domainpack.load_pack("classify-tuning")
_LOW_SIGNAL_TITLE_PATTERNS = list(_classify_tuning["low_signal_title_patterns"])
_LOW_SIGNAL_BODY_PATTERNS = list(_classify_tuning["low_signal_body_patterns"])

def is_low_signal_tier_candidate(art: dict) -> bool:
    """전시회 부스참가·시장보고서 PR·감사패 등 — 경쟁사 언급 없으면 tier1 부적합."""
    if art.get("competitors_mentioned"):
        return False
    title = art.get("title", "")
    body = art.get("first_paragraph", "") or art.get("summary", "")
    combined = title + " " + body
    if any(p in combined for p in _LOW_SIGNAL_TITLE_PATTERNS):
        return True
    if any(p in combined for p in _LOW_SIGNAL_BODY_PATTERNS):
        return True
    return False


# ── 티어 분류 ────────────────────────────────────────────────
def assign_tiers(arts: list[dict]) -> list[dict]:
    """tier 1=주요소식, 2=세부동향 부여. arts는 이미 score+recency 내림차순 정렬된 상태."""
    tier1_count = 0
    result = []
    for art in arts:
        score = art.get("relevance_score", 0)
        has_competitor = bool(art.get("competitors_mentioned"))

        # 저신호 전시회/보도자료는 tier1 제외
        if is_low_signal_tier_candidate(art):
            is_tier1 = False
        else:
            is_tier1 = (score >= TIER1_SCORE_THRESHOLD or has_competitor) and tier1_count < TIER1_CAT_MAX

        if is_tier1:
            tier1_count += 1
        result.append({**art, "_tier": 1 if is_tier1 else 2})
    return result


def cross_category_dedup(by_category: dict[str, list[dict]],
                         cat_order: list[str]) -> dict[str, list[dict]]:
    """동일 URL이 여러 카테고리에 걸쳐있으면 우선순위 카테고리에만 남긴다.
    cat_order 앞쪽 카테고리가 우선 (company-profile.yaml 순서)."""
    seen_urls: set[str] = set()
    result: dict[str, list[dict]] = {}
    for cat in cat_order + [c for c in by_category if c not in cat_order]:
        if cat not in by_category:
            continue
        deduped = []
        for art in by_category[cat]:
            url = art.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            deduped.append(art)
        result[cat] = deduped
    return result


# ── 팩트 구조화 ──────────────────────────────────────────────
def article_id(article: dict) -> str:
    """기사 안정 id — url 해시 기반. 같은 기사가 복수 카테고리에 등장해도 동일 id.

    synthesizer가 이 id를 ref로 출력하면 resolve-refs.py가 url/매체/날짜를
    결정론적으로 조인한다 (LLM이 URL을 복사하다 누락/중복 내는 문제 차단).
    """
    key = article.get("url") or article.get("title", "")
    return "a" + hashlib.md5(key.encode("utf-8")).hexdigest()[:6]


def extract_fact(article: dict) -> dict:
    """기사에서 구조화된 팩트 추출 (결정론적 — 첫 문단 기반)"""
    first_para = article.get("first_paragraph", "")
    title = article.get("title", "")

    # 첫 문단이 없으면 full_text 첫 300자
    if not first_para:
        first_para = article.get("full_text", "")[:300]

    tier = article.get("_tier", 2)
    # tier2 = 헤드라인 나열만 → summary 불필요 → 토큰 절감
    summary = first_para.strip()[:200] if tier == 1 else ""

    return {
        "id": article_id(article),
        "title": title,
        "summary": summary,
        "source_name": article.get("source_name", ""),
        "source_url": article.get("url", ""),
        "source_date": article.get("published_date", ""),
        "language": article.get("language", ""),
        "word_count": article.get("word_count", 0),
        "competitors_mentioned": article.get("competitors_mentioned", []),
        "relevance_score": article.get("relevance_score", 0),
        "tier": article.get("_tier", 2),  # 1=주요소식, 2=세부동향
        "confidence": "FACT",
    }


# ── 메인 집계 ────────────────────────────────────────────────
def aggregate(date_str: str, hours: int = 24):

    input_path = PROCESSED_DIR / f"classified-{date_str}.json"
    if not input_path.exists():
        print(f"ERROR: {input_path} not found. Run classify.py first.")
        sys.exit(1)

    data = load_json(input_path)

    articles = data.get("articles", [])
    profile = load_company_profile()
    categories_config = profile.get("categories", {})

    # 카테고리 순서 (company-profile.yaml 순서 유지)
    cat_order = list(categories_config.keys())

    # 카테고리별 그룹핑
    by_category: dict[str, list[dict]] = {}
    for art in articles:
        cats = art.get("categories", ["uncategorized"])
        for cat in cats:
            by_category.setdefault(cat, []).append(art)

    # 각 카테고리 내: score+recency 정렬 → title 중복 제거 (캡 없음 — 누락 방지)
    for cat in by_category:
        by_category[cat].sort(key=lambda a: sort_key(a, date_str), reverse=True)
        by_category[cat] = dedup_articles(by_category[cat])

    # 카테고리 간 URL 중복 제거: 동일 기사가 여러 카테고리에 걸쳐있으면 우선순위 카테고리 1곳만
    by_category = cross_category_dedup(by_category, cat_order)

    # tier 부여: tier1=주요소식 (score≥4 or 경쟁사 언급), tier2=세부동향
    for cat in by_category:
        by_category[cat] = assign_tiers(by_category[cat])

    # 구조화된 출력 생성
    category_facts = []
    total_facts = 0
    total_tier1 = 0
    total_tier2 = 0

    for cat_id in cat_order:
        if cat_id not in by_category:
            continue
        arts = by_category[cat_id]
        cat_info = categories_config.get(cat_id, {})

        facts = [extract_fact(a) for a in arts]
        t1 = sum(1 for f in facts if f["tier"] == 1)
        t2 = len(facts) - t1
        total_facts += len(facts)
        total_tier1 += t1
        total_tier2 += t2

        category_facts.append({
            "category_id": cat_id,
            "category_name": cat_info.get("label_ko", cat_id),
            "article_count": len(facts),
            "tier1_count": t1,
            "tier2_count": t2,
            "facts": facts,
        })

    # uncategorized 추가
    if "uncategorized" in by_category:
        uncat = by_category["uncategorized"]
        facts = [extract_fact(a) for a in uncat]
        total_facts += len(facts)
        t1_uncat = sum(1 for f in facts if f["tier"] == 1)
        category_facts.append({
            "category_id": "uncategorized",
            "category_name": "미분류",
            "article_count": len(facts),
            "tier1_count": t1_uncat,
            "tier2_count": len(facts) - t1_uncat,
            "facts": facts,
        })

    # 경쟁사별 기사 목록 (헤드라인 그룹핑용)
    # competitors_mentioned 는 리드 매칭 포함(관련성 점수용) — 그룹핑은 더 엄격하게
    # 제목에 회사명이 있을 때만. 리드 비교 언급(예: 중국 제조 기사 속 Tesla)이
    # 경쟁사 동향 그룹을 오염시키는 것 방지.
    import re as _re
    competitors_config = profile.get("competitors", [])
    comp_aliases = {c.get("name", ""): [c.get("name", "")] + c.get("aliases", [])
                    for c in competitors_config}

    def _name_in_title(name: str, title: str, art: dict | None = None) -> bool:
        """경쟁사명이 제목에 있는지 확인. 한글 alias는 본문 앞부분(80자)도 검사."""
        title_l = title.lower()
        fp_l = (art.get("first_paragraph", "") or "")[:80].lower() if art else ""
        _alpha_re = _re.compile(r"^[a-z]{1,12}$")
        for n in comp_aliases.get(name, [name]):
            n_l = n.lower()
            has_hangul = any('가' <= c <= '힣' for c in n_l)
            # 짧은 약어(1~4자) 또는 순수 영문명(12자 이하)은 단어 경계 매칭
            if _re.fullmatch(r"[a-z0-9.\-]{1,4}", n_l) or _alpha_re.match(n_l):
                if _re.search(r"(?<![a-z0-9])" + _re.escape(n_l) + r"(?![a-z0-9])", title_l):
                    return True
            elif n_l in title_l:
                return True
            # 한글 alias는 제목에 없어도 본문 앞 80자까지 검사
            if has_hangul and fp_l and n_l in fp_l:
                return True
        return False

    competitor_articles: dict[str, list[dict]] = {}
    for art in articles:
        for comp in art.get("competitors_mentioned", []):
            if not _name_in_title(comp, art.get("title", ""), art):
                continue
            competitor_articles.setdefault(comp, []).append({
                "id": article_id(art),
                "title": art["title"],
                "url": art["url"],
                "source_name": art.get("source_name", ""),
                "published_date": art.get("published_date", ""),
            })

    # 출력
    output = {
        "date": date_str,
        "total_articles": len(articles),
        "total_facts": total_facts,
        "total_tier1": total_tier1,
        "total_tier2": total_tier2,
        "categories": category_facts,
        "competitor_articles": competitor_articles,
        "metadata": {
            "classified_from": str(input_path),
            "dedup_threshold": 0.7,
            "hours": hours,
            "tier1_threshold": TIER1_SCORE_THRESHOLD,
        }
    }

    out_path = PROCESSED_DIR / f"newsletter-facts-{date_str}.json"
    save_json(out_path, output)

    # 리포트
    print(f"=== Aggregation Report ({date_str}, {hours}h) ===")
    print(f"Input:  {len(articles)} classified articles")
    print(f"Output: {total_facts} facts ({total_tier1} tier1/주요소식, {total_tier2} tier2/세부동향)")
    print(f"\nCategory breakdown:")
    for cf in category_facts:
        print(f"  {cf['category_name']} ({cf['category_id']}): "
              f"{cf['article_count']} total (t1={cf['tier1_count']}, t2={cf['tier2_count']})")
    print(f"\nCompetitor headlines:")
    for comp, arts in competitor_articles.items():
        print(f"  {comp}: {len(arts)} articles")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", default=date.today().isoformat())
    parser.add_argument("--hours", type=int, default=24,
                        help="수집 시간 범위 (기본 24h). 주간=168h. MAX_PER_CAT 스케일에 사용.")
    args = parser.parse_args()
    aggregate(args.date, args.hours)
