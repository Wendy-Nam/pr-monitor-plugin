"""classify.py 결정론적 분류 로직 스모크 테스트.

키워드 매칭, 자사 언급 감지, 카테고리 할당, 경쟁사 매칭, relevance score.
실제 config/keywords.yaml 없이 인라인 fixture로 테스트.
"""
# pylint: disable=redefined-outer-name,import-outside-toplevel


# 별칭이 명시된 프로필 — build_self_patterns 가 도메인 의존 형태소 치환을
# 하드코딩하지 않고 company.aliases 를 그대로 패턴화하는지 검증.
ALIASED_PROFILE = {
    "company": {
        "name": "콘토소 모터스",
        "aliases": ["Contoso Motors", "Contoso", "콘토소"],
    }
}


class TestBuildSelfPatterns:
    def test_generates_company_name(self, sample_profile):
        from scripts.pipeline.classify import build_self_patterns

        patterns = build_self_patterns(sample_profile)
        assert "에이콤로보틱스" in patterns

    def test_generates_short_name(self, sample_profile):
        from scripts.pipeline.classify import build_self_patterns

        patterns = build_self_patterns(sample_profile)
        # company_name[:5] = "에이콤로보" (회사명 앞 5글자 — 제목 축약형 후보)
        assert "에이콤로보" in patterns

    def test_uses_profile_aliases(self):
        from scripts.pipeline.classify import build_self_patterns

        patterns = build_self_patterns(ALIASED_PROFILE)
        # 프로필에 명시된 별칭(영문명·축약형)이 모두 패턴에 포함돼야 한다.
        assert "콘토소 모터스" in patterns          # 회사명
        assert "contoso motors" in patterns         # 영문 별칭 (lower)
        assert "contoso" in patterns
        assert "콘토소" in patterns


class TestIsSelfMention:
    def test_direct_mention_detected(self, sample_profile):
        from scripts.pipeline.classify import build_self_patterns, is_self_mention

        patterns = build_self_patterns(sample_profile)
        assert is_self_mention("에이콤로보틱스, 신제품 출시 예정", "기사 본문입니다.", patterns)

    def test_short_name_detected(self, sample_profile):
        from scripts.pipeline.classify import build_self_patterns, is_self_mention

        patterns = build_self_patterns(sample_profile)
        assert is_self_mention("에이콤로보, 협동로봇 신제품", "본문 텍스트", patterns)

    def test_competitor_not_self(self, sample_profile):
        from scripts.pipeline.classify import build_self_patterns, is_self_mention

        patterns = build_self_patterns(sample_profile)
        assert not is_self_mention("두산로보틱스, 시장 선도", "경쟁사 기사입니다.", patterns)

    def test_empty_text_not_self(self, sample_profile):
        from scripts.pipeline.classify import build_self_patterns, is_self_mention

        patterns = build_self_patterns(sample_profile)
        assert not is_self_mention("", "", patterns)


class TestMatchesAny:
    def test_matches_existing_keyword(self):
        from scripts.pipeline.classify import matches_any

        result = matches_any("협동로봇 시장 성장", ["협동로봇", "cobot", "AI"])
        assert "협동로봇" in result
        assert len(result) >= 1

    def test_no_match_returns_empty(self):
        from scripts.pipeline.classify import matches_any

        result = matches_any("일반 뉴스 기사입니다.", ["협동로봇", "humanoid"])
        assert result == []

    def test_case_insensitive(self):
        from scripts.pipeline.classify import matches_any

        result = matches_any("Cobot Market Growth", ["cobot"])
        assert result == ["cobot"]


class TestTermIn:
    """단어 경계 매칭 — 짧은 영문 약어(UR/ABB/AI)가 일반 단어 안에서 오검출되면 안 됨."""

    def test_short_alias_standalone_matched(self):
        from scripts.pipeline.classify import term_in

        assert term_in("ur", "ur robotics launches new cobot")
        assert term_in("abb", "abb robotics partners with")
        assert term_in("ai", "ai 로봇 출시")

    def test_short_alias_inside_word_not_matched(self):
        from scripts.pipeline.classify import term_in

        # 'UR' 이 "during", "manufacturing" 안에서 잡히던 버그 (경쟁사 38건 오매칭 원인)
        assert not term_in("ur", "during the manufacturing event")
        # 'AI' 가 "again" 안에서
        assert not term_in("ai", "again and again")
        # 'ABB' 가 "grabbing" 안에서
        assert not term_in("abb", "grabbing attention")

    def test_long_term_substring_matched(self):
        from scripts.pipeline.classify import term_in

        # 긴 영문/한글은 부분문자열 매칭 유지
        assert term_in("boston dynamics", "the boston dynamics atlas robot")
        assert term_in("협동로봇", "신형 협동로봇을 출시했다")


class TestAssignCategories:
    def test_humanoid_keyword(self, sample_profile):
        from scripts.pipeline.classify import assign_categories

        cats = assign_categories("Boston Dynamics 신형 휴머노이드 공개", "", sample_profile)
        assert "humanoid" in cats

    def test_generic_keyword_alone_not_categorized(self):
        from scripts.pipeline.classify import assign_categories

        # 빅웨이브 케이스: robotics/automation 같은 범용어만으로는 카테고리 부여 금지
        profile = {
            "categories": {
                "amr": {
                    "label_ko": "AMR",
                    "watch_keywords": ["robotics", "automation", "AGV"],
                    "key_players": [],
                }
            }
        }
        cats = assign_categories(
            "Bigwave Robotics to Participate in Largest Automation Exhibition",
            "", profile,
        )
        assert cats == ["uncategorized"]
        # 고유 키워드(AGV)가 있으면 정상 분류
        cats2 = assign_categories("신형 AGV 물류 로봇 출시", "", profile)
        assert "amr" in cats2

    def test_keyword_only_in_deep_body_not_categorized(self, sample_profile):
        from scripts.pipeline.classify import assign_categories

        # 리드(앞 400자) 밖 본문에만 등장하는 키워드는 카테고리 결정에 쓰지 않음
        long_body = "일반 기업 행사 소식입니다. " * 60 + " 휴머노이드 로봇 언급"
        cats = assign_categories("어느 회사 전시회 참가 소식", long_body, sample_profile)
        assert cats == ["uncategorized"]

    def test_cobot_keyword(self, sample_profile):
        from scripts.pipeline.classify import assign_categories

        cats = assign_categories("Universal Robots 신형 협동로봇 출시", "", sample_profile)
        assert "cobots" in cats

    def test_multiple_categories(self, sample_profile):
        from scripts.pipeline.classify import assign_categories

        cats = assign_categories(
            "협동로봇 스타트업, 시리즈 A 500억 투자 유치", "", sample_profile
        )
        assert "cobots" in cats
        # "투자" 키워드로 funding도 할당될 수 있음
        assert "funding" in cats

    def test_uncategorized_fallback(self, sample_profile):
        from scripts.pipeline.classify import assign_categories

        cats = assign_categories("오늘 날씨는 맑음", "", sample_profile)
        assert cats == ["uncategorized"]


class TestMatchCompetitors:
    def test_direct_name(self, sample_profile):
        from scripts.pipeline.classify import match_competitors

        comps = match_competitors("두산로보틱스, 신형 협동로봇 공개", "", sample_profile)
        assert "두산로보틱스" in comps

    def test_alias(self, sample_profile):
        from scripts.pipeline.classify import match_competitors

        comps = match_competitors("Doosan Robotics, 협동로봇 미국 진출", "", sample_profile)
        assert "두산로보틱스" in comps

    def test_no_competitor(self, sample_profile):
        from scripts.pipeline.classify import match_competitors

        comps = match_competitors("일반 경제 뉴스입니다.", "", sample_profile)
        assert comps == []

    def test_competitor_listing_in_lead_excluded(self, sample_profile):
        from scripts.pipeline.classify import match_competitors

        # 리드에 나열·비교("등과 함께")로 등장하면 비교 대상 → 배제
        comps = match_competitors(
            "어느 스타트업, 자동화 전시회 참가",
            "이 회사는 두산로보틱스, ABB 등과 함께 부스를 차린다",
            sample_profile,
        )
        assert comps == []

    def test_competitor_subject_in_lead_matched(self, sample_profile):
        from scripts.pipeline.classify import match_competitors

        # 제목엔 없어도 리드에서 주체로 등장하면 경쟁사로 인정
        comps = match_competitors(
            "협동로봇 시장 침체…주요 업체 실적 부진",
            "두산로보틱스가 올해 협동로봇 매출 부진을 겪고 있다고 밝혔다.",
            sample_profile,
        )
        assert "두산로보틱스" in comps

    def test_alias_word_boundary_not_in_common_word(self, sample_profile):
        from scripts.pipeline.classify import match_competitors

        # 'ABB' 가 "grabbing" 같은 일반 단어 안에서 경쟁사로 오검출되면 안 됨
        comps = match_competitors("Startup grabbing market attention", "", sample_profile)
        assert "ABB" not in comps


class TestSamsungBoost:
    def test_samsung_robot_investment_boosted(self):
        from scripts.pipeline.classify import stakeholder_boost

        # 삼성의 타 로봇사 투자 = 자사(최대주주 관계) 직접 이해관계
        assert stakeholder_boost("[단독]삼성전자, 美 산업용 로봇 스타트업 '스탠다드봇' 투자") == 2
        assert stakeholder_boost("Samsung invests in Standard Bots") == 2

    def test_samsung_appliance_not_boosted(self):
        from scripts.pipeline.classify import stakeholder_boost

        assert stakeholder_boost("삼성전자, 로봇청소기 '비스포크 AI 스팀' 출시") == 0
        assert stakeholder_boost("삼성전자, 갤럭시 신제품 공개") == 0

    def test_no_samsung_no_boost(self):
        from scripts.pipeline.classify import stakeholder_boost

        assert stakeholder_boost("두산로보틱스, 협동로봇 신제품") == 0


class TestGateDecision:
    """재현율 우선 게이트 — 카테고리 매칭 기사는 저점수여도 안 버린다."""

    def test_high_score_included(self):
        from scripts.pipeline.classify import gate_decision
        assert gate_decision(3, False) == "include"
        assert gate_decision(5, True) == "include"

    def test_category_match_survives_low_score(self):
        """핵심 회귀: 카테고리에 진짜 걸린 기사는 relevance<2 여도 manual_review 로 살림
        (키워드가 좁아 진짜 업계 기사가 조용히 탈락하던 문제)."""
        from scripts.pipeline.classify import gate_decision
        assert gate_decision(1, True) == "manual_review"
        assert gate_decision(0, True) == "manual_review"

    def test_uncategorized_noise_excluded(self):
        """미분류 잡음은 여전히 저점수에서 제외 — 무분별 개방 아님."""
        from scripts.pipeline.classify import gate_decision
        assert gate_decision(1, False) == "exclude"
        assert gate_decision(0, False) == "exclude"

    def test_midscore_manual_review(self):
        from scripts.pipeline.classify import gate_decision
        assert gate_decision(2, False) == "manual_review"


class TestCalcRelevance:
    def test_high_relevance(self):
        from scripts.pipeline.classify import calc_relevance

        article = {"word_count": 800}
        score = calc_relevance(
            article, ["로봇", "AI", "협동"], ["cobots"], ["두산로보틱스"]
        )
        # 3 boost hits(3) + competitors(2) + categories(1) = 6 → capped to 5
        assert score == 5

    def test_low_relevance_short_article(self):
        from scripts.pipeline.classify import calc_relevance

        article = {"word_count": 200}
        score = calc_relevance(article, [], ["cobots"], [])
        # 0 boost(0) + 0 competitors(0) + categories(1) - short penalty(1) = 0
        assert score == 0

    def test_medium_relevance(self):
        from scripts.pipeline.classify import calc_relevance

        article = {"word_count": 600}
        score = calc_relevance(article, ["로봇"], ["cobots"], [])
        # 1 boost(1) + categories(1) = 2 (boost 1개당 +1, 2개 미만)
        assert score == 2

    def test_short_article_penalty(self):
        from scripts.pipeline.classify import calc_relevance

        article = {"word_count": 200}
        score = calc_relevance(article, ["로봇"], ["cobots"], [])
        # 1 boost(1) + categories(1) - short(1) = 1 (boost 1개당 +1, 2개 미만)
        assert score == 1
