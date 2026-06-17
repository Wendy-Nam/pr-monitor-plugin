#!/usr/bin/env python3
"""Step 3: 결정론적 기사 분류 (LLM 호출 없음)

입력: data/processed/extracted-{date}.json
설정: config/keywords.yaml, config/company-profile.yaml
출력: data/processed/classified-{date}.json
      data/processed/pr-articles-{date}.json (자사 언급 기사 분리)

분류 기준:
  1. strong_exclude 키워드 → 즉시 제외
  2. 블로그/비공식 채널 → 즉시 제외
  3. self_mention 감지 → PR 모니터 분리 (인사이트에서 제외)
  4. boost 키워드 매칭 → relevance_score 산정
  5. 카테고리 할당 (company-profile.yaml 기반)
  6. 경쟁사 매칭
"""

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ (lib import)
from lib.common import (
    CONFIG_DIR, PROCESSED_DIR, is_blog, load_json, load_yaml, save_json,
)

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import domainpack

# 도메인팩 — classify-tuning.yaml 에서 분류 튜닝 파라미터 로드 (하드코딩 외부화).
_TUNING = domainpack.load_pack("classify-tuning")


# ── 설정 로드 ───────────────────────────────────────────────
def load_keywords() -> dict:
    return load_yaml(CONFIG_DIR / "keywords.yaml")

def load_company_profile() -> dict:
    return load_yaml(CONFIG_DIR / "company-profile.yaml")


# ── 자사 언급 감지 ──────────────────────────────────────────
def build_self_patterns(profile: dict) -> list[str]:
    """자사명 + 별칭으로 부분 매칭 패턴 생성.

    별칭은 company-profile.yaml 의 company.aliases 에서 읽는다 (도메인 의존
    형태소 치환을 하드코딩하지 않는다). aliases 미정의 시에는 회사명과 그
    앞부분(축약형 후보)만으로 폴백한다.
    """
    company = profile.get("company", {})
    company_name = company.get("name", "")
    patterns = []
    if company_name:
        patterns.append(company_name)            # 전체 회사명
        # 축약형 후보 — 제목에서 잘리는 패턴 (회사명 앞부분).
        if len(company_name) > 5:
            patterns.append(company_name[:5])
        # 별칭은 프로필에 명시된 것만 사용 (영문명·축약형 포함 가능).
        for a in company.get("aliases") or []:
            if a:
                patterns.append(a)
    return [p.lower() for p in patterns if p]


# 리스크 키워드 — 제목에 있으면 자사명 스캔을 본문 전체로 확장.
# 부정 보도가 본문 깊은 곳에서 자사를 스치는 경우 인사이트 풀(뉴스레터)로
# 새는 것을 막는다. 일반 기사는 앞 2000자 유지 (업계 나열 언급 과차단 방지).
RISK_TITLE_KW = list(_TUNING["risk_title_keywords"])

# 이해관계자 간접 매칭 — classify-tuning stakeholder_boosts 에서 구성.
# 자사 이해관계자(예: 최대주주)가 법적 연루 기사에서 자사를 스칠 때 PR 풀로
# 보내기 위한 보수 스캔. 회사명·산업("삼성"+"로봇")을 하드코딩하지 않고,
# 트리거 용어는 stakeholder_boosts[*].match, 법적 표지는 risk_title_keywords 에서 읽는다.
# 옵트인: stakeholder 엔트리에 indirect_match: true 가 있으면 그 엔트리만,
# 명시가 하나도 없으면 모든 stakeholder 가 간접 매칭에 참여(기존 동작 보존).
_STAKEHOLDER_BOOSTS = list(_TUNING.get("stakeholder_boosts", []))
_opt_in = [b for b in _STAKEHOLDER_BOOSTS if b.get("indirect_match")]
_INDIRECT_STAKEHOLDERS = _opt_in if _opt_in else _STAKEHOLDER_BOOSTS
_INDIRECT_TRIGGER_TERMS = [
    t.lower() for b in _INDIRECT_STAKEHOLDERS for t in (b.get("match") or [])
]


def is_self_mention(title: str, text: str, patterns: list[str]) -> bool:
    combined = (title + " " + text[:2000]).lower()
    # 직접 매칭
    if any(p in combined for p in patterns):
        return True
    # 리스크 기사 보수 스캔 — 제목에 리스크 키워드가 있으면 본문 전체에서 자사명 검사
    if any(k in title for k in RISK_TITLE_KW):
        full = (title + " " + text).lower()
        if any(p in full for p in patterns):
            return True
    # 간접 매칭 — 이해관계자 언급 + 법적 연루 표지 조합 (자사 연루 가능성).
    # 트리거 용어·법적 표지 모두 classify-tuning 도메인팩에서 온다.
    if _INDIRECT_TRIGGER_TERMS and any(t in combined for t in _INDIRECT_TRIGGER_TERMS):
        if any(k.lower() in combined for k in RISK_TITLE_KW):
            return True
    return False


# ── 키워드 매칭 ─────────────────────────────────────────────
def matches_any(text: str, keywords: list[str]) -> list[str]:
    """text 안에 포함된 키워드 목록 반환 (case-insensitive)"""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


# 판정 영역 — 제목 + 리드. 본문 깊은 곳의 비교/나열 언급으로 인한
# 오분류(경쟁사 스침, 범용어 과매칭)를 막기 위해 앞부분만 본다.
# 400은 재현율 손실이 컸음(클로봇·휴로틱스 등 누락) — 범용어 제외가
# 정밀도를 지키므로 800까지 넓혀도 오분류 회귀 없음(06-11 데이터 검증).
SUBJECT_LEAD_CHARS = 800

# 범용어 — 어느 카테고리 기사에나 등장하므로 카테고리를 단독으로 결정하지 못한다.
# (예: "Bigwave Robotics ... Automation Exhibition" → robotics/automation 만으로 amr·humanoid 분류되던 버그)
# 카테고리별 고유 키워드(복합어 포함, 예: "NVIDIA Omniverse", "Cosmos")는 그대로 specific 으로 인정된다.
GENERIC_CATEGORY_TERMS = set(_TUNING["generic_category_terms"])
# M&A·투자 라우팅 (선택) — {keywords:[...], target:"<category_id>"}. 도메인팩이 지정하면
# 그 키워드(지분 인수/투자/매각 등)에 걸린 기사를 제품 카테고리 대신 target 으로 보낸다.
_MA_ROUTE = _TUNING.get("ma_route", {}) or {}


def _subject_text(title: str, text: str) -> str:
    """카테고리·경쟁사 판정용 영역: 제목 + 리드(본문 앞부분)."""
    return (title + " " + text[:SUBJECT_LEAD_CHARS]).lower()


# 짧은 영문/숫자 약어(UR, ABB, AMR, AGV, AI, BD, 1X …)는 단어 경계로 매칭한다.
# 부분문자열 매칭 시 "during"→UR, "automation"→AI 처럼 일반 단어 안에서 오검출된다.
# 긴 영문명(FANUC, Tesla, Yaskawa …)도 같은 이유로 단어 경계 매칭.
_SHORT_TOKEN_RE = re.compile(r"^[a-z0-9.\-]{1,4}$")
_ALPHA_TOKEN_RE = re.compile(r"^[a-z]{1,12}$")

def term_find(term: str, text_lower: str) -> int:
    """term(소문자)의 등장 위치를 반환(없으면 -1).

    짧은 영문/숫자 약어(1~4자) 및 순수 영문명(12자 이하)은 단어 경계 매칭,
    그 외(한글·공백 포함·긴 문장)는 부분문자열 매칭.
    """
    if _SHORT_TOKEN_RE.match(term) or _ALPHA_TOKEN_RE.match(term):
        m = re.search(
            r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text_lower
        )
        return m.start() if m else -1
    return text_lower.find(term)


def term_in(term: str, text_lower: str) -> bool:
    """term(소문자)이 text_lower 안에 의미 단위로 등장하는지."""
    return term_find(term, text_lower) >= 0


# 경쟁사 리드 검사 범위 — 제목에 회사명이 없어도 본문 주체가 경쟁사인 기사를 잡되,
# 비교·나열로 스친 회사는 배제하기 위해 앞부분만 본다.
COMPETITOR_LEAD_CHARS = 300

# 나열·비교 표지 — 회사명 직후 짧은 범위에 등장하면 그 회사는 기사 주체가 아니라
# 비교 대상이다. ("두산·ABB 등과 함께", "such as Boston Dynamics")
_LISTING_MARKERS = list(_TUNING["listing_markers"])


def _is_listing_context(text_lower: str, pos: int, name_len: int) -> bool:
    """회사명 직후 짧은 범위에 나열·비교 표지가 있으면 True (비교 대상으로 간주)."""
    after = text_lower[pos + name_len: pos + name_len + 12]
    return any(mk in after for mk in _LISTING_MARKERS)


# ── 카테고리 할당 ────────────────────────────────────────────
def assign_categories(title: str, text: str, profile: dict) -> list[str]:
    """기사 제목+리드 기반 카테고리 할당. 복수 가능.

    범용어(robot/automation/NVIDIA 등)는 카테고리 결정에서 제외 —
    고유 키워드(watch_keywords·key_players·label 중 범용어가 아닌 것)가
    제목/리드에 등장할 때만 해당 카테고리를 부여한다.
    """
    categories = profile.get("categories", {})
    subject = _subject_text(title, text)
    assigned = []

    for cat_id, cat_info in categories.items():
        watch = cat_info.get("watch_keywords", [])
        key_players = cat_info.get("key_players", [])
        label = cat_info.get("label_ko", "")

        all_terms = [w.lower() for w in watch] + [p.lower() for p in key_players]
        if label:
            all_terms.append(label.lower())

        # 범용어 제외 — 카테고리 고유 키워드만 판정에 사용
        specific_terms = [t for t in all_terms if t not in GENERIC_CATEGORY_TERMS]

        if any(term_in(t, subject) for t in specific_terms):
            assigned.append(cat_id)

    # M&A·투자 라우팅 (도메인팩 ma_route 선택) — 지분 인수/투자/매각은 제품 카테고리가
    # 아니라 자금/M&A 사건이므로 지정 target 카테고리로 보낸다 (설정 없으면 무동작 = 중립).
    if _MA_ROUTE.get("target") in categories and any(
            term_in(k.lower(), subject) for k in _MA_ROUTE.get("keywords", [])):
        return [_MA_ROUTE["target"]]

    # 기본 카테고리 — 아무 것도 안 걸리면 source_query 기반 폴백
    return assigned if assigned else ["uncategorized"]


# ── 경쟁사 매칭 ─────────────────────────────────────────────
def match_competitors(title: str, text: str, profile: dict) -> list[str]:
    """기사 주체로 등장한 경쟁사 목록.

    - 제목에 회사명이 있으면 확정(주체일 가능성 높음).
    - 제목에 없어도 리드(앞 300자)에 등장하면 인정하되, 회사명 직후에
      나열·비교 표지("등과 함께", "such as")가 있으면 비교 대상으로 보고 배제.
    이렇게 "협동로봇 시장 침체…두산 실적 부진"(주체)은 잡고,
    "빅웨이브가 두산·BD 등과 함께 참가"(나열)는 배제한다.
    """
    competitors = profile.get("competitors", [])
    title_lower = title.lower()
    lead_lower = text[:COMPETITOR_LEAD_CHARS].lower()
    matched = []

    for comp in competitors:
        name = comp.get("name", "")
        aliases = comp.get("aliases", [])
        all_names = [name.lower()] + [a.lower() for a in aliases]

        # 1) 제목 매칭 → 확정
        if any(term_in(n, title_lower) for n in all_names):
            matched.append(name)
            continue

        # 2) 리드 매칭 → 나열·비교 맥락이 아닐 때만 인정
        for n in all_names:
            idx = term_find(n, lead_lower)
            if idx >= 0 and not _is_listing_context(lead_lower, idx, len(n)):
                matched.append(name)
                break

    return matched


# 이해관계자 가중치 — 주요 주주·핵심 공급사 등의 자사 분야 움직임은 직접 이해관계.
# classify-tuning stakeholder_boosts 에서 온다. 빈 목록이면(예시 도메인 기본값) 가중치 없음.
_STAKEHOLDER_BOOSTS = _TUNING.get("stakeholder_boosts") or []
_STAKEHOLDER_BOOST = _STAKEHOLDER_BOOSTS[0] if _STAKEHOLDER_BOOSTS else {}
_STAKEHOLDER_MATCH_TERMS = [t.lower() for t in (_STAKEHOLDER_BOOST.get("match") or [])]
_STAKEHOLDER_NOISE_TERMS = list(_STAKEHOLDER_BOOST.get("noise_terms") or [])
_STAKEHOLDER_BOOST_WEIGHT = _STAKEHOLDER_BOOST.get("weight", 0)


def stakeholder_boost(title: str) -> int:
    """제목에 이해관계자(최대주주 등) 언급 + 노이즈 아님 → 가중치.

    트리거 용어·노이즈·가중치 모두 classify-tuning stakeholder_boosts 에서 온다.
    이해관계자 미설정(빈 목록)이면 항상 0.
    """
    if not _STAKEHOLDER_MATCH_TERMS:
        return 0
    t = title.lower()
    if not any(m in t for m in _STAKEHOLDER_MATCH_TERMS):
        return 0
    if any(n in t for n in _STAKEHOLDER_NOISE_TERMS):
        return 0
    return _STAKEHOLDER_BOOST_WEIGHT


# ── 포함/제외 게이트 ─────────────────────────────────────────
def gate_decision(relevance: int, genuinely_categorized: bool) -> str:
    """include / manual_review / exclude.

    재현율 우선: 키워드 relevance 는 *비중(tier)* 을 정할 뿐 생사를 가르지 않는다.
    조직이 정의한 카테고리에 **진짜 매칭된** 기사는 점수가 낮아도 버리지 않고
    manual_review(=tier2 헤드라인)로 살린다 — "누락 없는 모니터링" 철학. 살아남은
    tier2 의 비중은 Haiku 중요도 채점(aggregate)이 재정하고, 잡음은 낮게 매겨 각주行으로
    내린다. 어느 카테고리에도 안 걸린(미분류) 잡음만 relevance<2 에서 제외한다.
    """
    if relevance >= 3:
        return "include"
    if relevance >= 2 or genuinely_categorized:
        return "manual_review"
    return "exclude"


# ── relevance_score 산정 ─────────────────────────────────────
def calc_relevance(article: dict, boost_hits: list[str],
                   categories: list[str], competitors: list[str]) -> int:
    """0~5 점. 높을수록 관련성 높음."""
    score = 0

    # boost 키워드 매칭 수 (2개 미만은 +1, 잡음 방지)
    if len(boost_hits) >= 3:
        score += 3
    elif len(boost_hits) >= 2:
        score += 2
    elif len(boost_hits) >= 1:
        score += 1

    # 경쟁사 매칭 — 경쟁사 기사는 기본적으로 중요
    if competitors:
        score += 2

    # 이해관계자(최대주주 등) 자사 분야 움직임 — 자사 직접 이해관계
    score += stakeholder_boost(article.get("title", ""))

    # 카테고리 매칭 (uncategorized 아닌 경우)
    if categories and categories != ["uncategorized"]:
        score += 1

    # tier1 소스 가산
    if article.get("_source_tier") == "tier1_google_news":
        score += 0  # tier는 이미 수집 단계에서 반영됨

    # word_count가 너무 짧으면 감점
    if (article.get("word_count") or 0) < 500:
        score = max(0, score - 1)

    # 전시회/박람회 단순 참가 기사 — 경쟁사 언급 없으면 감점
    _expo_patterns = list(_TUNING["expo_patterns"])
    if not competitors and any(p in article.get("title", "") for p in _expo_patterns):
        score = max(0, score - 1)

    return min(5, score)


# ── 메인 분류 로직 ───────────────────────────────────────────
def classify_articles(date_str: str):
    input_path = PROCESSED_DIR / f"extracted-{date_str}.json"
    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    data = load_json(input_path)

    articles = data.get("articles", [])
    kw = load_keywords()
    profile = load_company_profile()

    boost_keywords = kw.get("boost", [])
    strong_exclude = kw.get("strong_exclude", [])
    general_exclude = kw.get("exclude", [])
    self_patterns = build_self_patterns(profile)

    classified = []
    pr_articles = []
    excluded = []

    for art in articles:
        url = art.get("url", "")
        title = art.get("title", "")
        text = art.get("full_text", "")
        paras = art.get("paragraphs") or []
        if paras:
            p0 = paras[0]
            first_para = (p0.get("text", "") if isinstance(p0, dict) else str(p0))[:300]
        else:
            first_para = text[:300]

        # 1. 블로그 제외
        if is_blog(url):
            excluded.append({"url": url, "title": title, "reason": "blog_domain"})
            continue

        # 2. strong_exclude
        strong_hits = matches_any(title, strong_exclude)
        if strong_hits:
            excluded.append({"url": url, "title": title, "reason": f"strong_exclude: {strong_hits[0]}"})
            continue

        # 3. self_mention 감지
        self_mention = is_self_mention(title, text, self_patterns)

        # 4. boost 키워드 매칭
        boost_hits = matches_any(title + " " + text[:2000], boost_keywords)

        # 5. general_exclude (boost 없을 때만 적용)
        if not boost_hits:
            gen_hits = matches_any(title, general_exclude)
            if gen_hits:
                excluded.append({"url": url, "title": title, "reason": f"general_exclude: {gen_hits[0]}"})
                continue

        # 6. 카테고리 + 경쟁사
        categories = assign_categories(title, text, profile)
        competitors = match_competitors(title, text, profile)

        # 7. relevance_score
        relevance = calc_relevance(art, boost_hits, categories, competitors)

        # 8. uncategorized 캐치올 — relevance 계산 후 '기타산업(other_industrial)'으로 흡수.
        # uncategorized 로 남기면 카테고리 요약에 못 들어가 출처 번호만 받고 본문 미인용됨.
        # 캐치올 전에 '진짜 카테고리 매칭'이었는지 기록 — 아래 게이트 결정에 쓴다.
        genuinely_categorized = categories != ["uncategorized"]
        if categories == ["uncategorized"]:
            categories = ["other_industrial"]

        result = {
            "url": url,
            "title": title,
            "source_name": art.get("source_name", ""),
            "author": art.get("author"),
            "published_date": art.get("published_date", ""),
            "language": art.get("language", ""),
            "word_count": art.get("word_count", 0),
            "first_paragraph": first_para,
            "categories": categories,
            "competitors_mentioned": competitors,
            "relevance_score": relevance,
            "self_mention": self_mention,
            "boost_keywords_matched": boost_hits[:5],
            "decision": gate_decision(relevance, genuinely_categorized),
            "_source_query": art.get("_source_query"),
            "_source_tier": art.get("_source_tier"),
        }

        if self_mention:
            # PR 클리핑은 본문 전체 필요 (톤 판정·요약) — full_text 는 pr-articles 에만 유지.
            # classified 쪽은 first_paragraph 만 쓰므로 (aggregate.py) 본문 미포함 → extracted 와 이중 저장 방지.
            pr_articles.append({**result, "full_text": text})
            continue

        if result["decision"] != "exclude":
            classified.append(result)
        else:
            excluded.append({"url": url, "title": title, "reason": f"low_relevance ({relevance})"})

    # 정렬: relevance 높은 순
    classified.sort(key=lambda x: x["relevance_score"], reverse=True)

    # 출력
    out_classified = PROCESSED_DIR / f"classified-{date_str}.json"
    out_pr = PROCESSED_DIR / f"pr-articles-{date_str}.json"

    save_json(out_classified, {
        "date": date_str,
        "total_input": len(articles),
        "classified": len(classified),
        "pr_articles": len(pr_articles),
        "excluded": len(excluded),
        "articles": classified,
    })

    save_json(out_pr, {
        "date": date_str,
        "count": len(pr_articles),
        "articles": pr_articles,
    })

    # 리포트
    print(f"=== Classification Report ({date_str}) ===")
    print(f"Input:      {len(articles)} articles")
    print(f"Classified: {len(classified)} (insight pool)")
    print(f"PR/Self:    {len(pr_articles)} (self-mention → PR 모니터링)")
    print(f"Excluded:   {len(excluded)}")
    print(f"\nCategory distribution:")
    from collections import Counter
    cat_counts = Counter()
    for a in classified:
        for c in a["categories"]:
            cat_counts[c] += 1
    for cat, cnt in cat_counts.most_common():
        print(f"  {cat}: {cnt}")
    print(f"\nCompetitors mentioned:")
    comp_counts = Counter()
    for a in classified:
        for c in a["competitors_mentioned"]:
            comp_counts[c] += 1
    for comp, cnt in comp_counts.most_common():
        print(f"  {comp}: {cnt}")
    print(f"\nOutput: {out_classified}")
    print(f"PR:     {out_pr}")

    if excluded:
        print(f"\nExcluded articles:")
        for e in excluded:
            print(f"  [{e['reason']}] {e['title'][:60]}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        d = sys.argv[1]
    else:
        d = date.today().isoformat()
    classify_articles(d)
