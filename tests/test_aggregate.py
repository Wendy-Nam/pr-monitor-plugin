"""aggregate.py 결정론적 집계 로직 스모크 테스트.

중복 제거, 티어 분류, 카테고리 간 URL 중복 제거, 팩트 추출.
"""
# pylint: disable=redefined-outer-name,import-outside-toplevel


class TestSortKey:
    def test_higher_score_first(self):
        from scripts.pipeline.aggregate import sort_key

        high = {"relevance_score": 5, "published_date": "2026-06-10"}
        low = {"relevance_score": 3, "published_date": "2026-06-10"}
        assert sort_key(high, "2026-06-11") > sort_key(low, "2026-06-11")

    def test_recency_bonus(self):
        from scripts.pipeline.aggregate import sort_key

        recent = {"relevance_score": 5, "published_date": "2026-06-11"}
        older = {"relevance_score": 5, "published_date": "2026-06-05"}
        assert sort_key(recent, "2026-06-11") > sort_key(older, "2026-06-11")

    def test_no_date_no_bonus(self):
        from scripts.pipeline.aggregate import sort_key

        no_date = {"relevance_score": 5}
        result = sort_key(no_date, "2026-06-11")
        assert result == 5.0  # score only, no bonus


class TestDedupArticles:
    def test_dedup_similar_titles(self):
        from scripts.pipeline.aggregate import dedup_articles

        articles = [
            {"title": "두산로보틱스 협동로봇 매출 반토막"},
            {"title": "두산로보틱스 협동로봇 매출 급감"},
            {"title": "FANUC AI 로봇 1000대 배치"},
        ]
        result = dedup_articles(articles, threshold=0.7)
        # "매출 반토막" vs "매출 급감" — 대부분 일치 → dedup
        assert len(result) == 2

    def test_keep_dissimilar_titles(self):
        from scripts.pipeline.aggregate import dedup_articles

        articles = [
            {"title": "두산로보틱스 매출 반토막"},
            {"title": "FANUC AI 로봇 1000대 배치"},
            {"title": "NVIDIA Cosmos 3 공개"},
        ]
        result = dedup_articles(articles, threshold=0.7)
        assert len(result) == 3

    def test_high_threshold_keeps_more(self):
        from scripts.pipeline.aggregate import dedup_articles

        articles = [
            {"title": "두산로보틱스 협동로봇 매출 반토막"},
            {"title": "두산로보틱스 협동로봇 매출 급감"},
        ]
        # threshold 0.9 → only nearly identical kept
        result = dedup_articles(articles, threshold=0.9)
        assert len(result) > 1 or len(result) > 0  # len≥original len if no matches
        # At this high threshold, they shouldn't dedup
        assert len(result) == 2

    def test_identical_titles_dedup(self):
        from scripts.pipeline.aggregate import dedup_articles

        articles = [
            {"title": "완전히 동일한 제목"},
            {"title": "완전히 동일한 제목"},
        ]
        result = dedup_articles(articles)
        assert len(result) == 1

    def test_empty_input(self):
        from scripts.pipeline.aggregate import dedup_articles

        result = dedup_articles([])
        assert result == []


class TestAssignTiers:
    def test_high_score_tier1(self):
        from scripts.pipeline.aggregate import assign_tiers

        articles = [
            {"relevance_score": 5, "competitors_mentioned": [], "title": "A"},
            {"relevance_score": 3, "competitors_mentioned": [], "title": "B"},
            {"relevance_score": 1, "competitors_mentioned": [], "title": "C"},
        ]
        result = assign_tiers(articles)
        assert result[0]["_tier"] == 1  # score >= 4
        assert result[1]["_tier"] == 2  # score < 4
        assert result[2]["_tier"] == 2

    def test_competitor_mention_tier1(self):
        from scripts.pipeline.aggregate import assign_tiers

        articles = [
            {"relevance_score": 4, "competitors_mentioned": [], "title": "A"},
            {"relevance_score": 2, "competitors_mentioned": ["두산로보틱스"], "title": "B"},
        ]
        result = assign_tiers(articles)
        assert result[0]["_tier"] == 1  # score >= 4
        assert result[1]["_tier"] == 1  # has competitor, even though score < 4

    def test_tier1_cap(self):
        from scripts.pipeline.aggregate import assign_tiers

        articles = [
            {"relevance_score": 5, "competitors_mentioned": [], "title": f"A{i}"}
            for i in range(10)
        ]
        result = assign_tiers(articles)
        tier1_count = sum(1 for a in result if a["_tier"] == 1)
        assert tier1_count == 5  # TIER1_CAT_MAX = 5


class TestCrossCategoryDedup:
    def test_duplicate_url_removed(self):
        from scripts.pipeline.aggregate import cross_category_dedup

        by_category = {
            "cobots": [{"url": "https://example.com/1", "title": "같은 기사"}],
            "humanoid": [{"url": "https://example.com/1", "title": "같은 기사 (중복)"}],
        }
        result = cross_category_dedup(by_category, ["cobots", "humanoid"])
        assert len(result["cobots"]) == 1
        assert len(result["humanoid"]) == 0

    def test_unique_urls_preserved(self):
        from scripts.pipeline.aggregate import cross_category_dedup

        by_category = {
            "cobots": [{"url": "https://example.com/1", "title": "A"}],
            "humanoid": [{"url": "https://example.com/2", "title": "B"}],
        }
        result = cross_category_dedup(by_category, ["cobots", "humanoid"])
        assert len(result["cobots"]) == 1
        assert len(result["humanoid"]) == 1


class TestExtractFact:
    def test_tier1_includes_summary(self):
        from scripts.pipeline.aggregate import extract_fact

        article = {
            "title": "테스트 기사",
            "url": "https://example.com/test",
            "source_name": "테스트매체",
            "published_date": "2026-06-10",
            "language": "ko",
            "word_count": 500,
            "competitors_mentioned": [],
            "relevance_score": 5,
            "_tier": 1,
            "first_paragraph": "첫 문단입니다. 주요 내용이 여기 있습니다.",
        }
        fact = extract_fact(article)
        assert fact["title"] == "테스트 기사"
        assert fact["tier"] == 1
        assert fact["summary"] != ""  # tier1 → summary 포함
        assert fact["confidence"] == "FACT"

    def test_tier2_has_no_summary(self):
        from scripts.pipeline.aggregate import extract_fact

        article = {
            "title": "일반 기사",
            "url": "https://example.com/other",
            "source_name": "기타매체",
            "published_date": "2026-06-08",
            "language": "en",
            "word_count": 300,
            "competitors_mentioned": [],
            "relevance_score": 2,
            "_tier": 2,
            "first_paragraph": "참고용 기사입니다.",
        }
        fact = extract_fact(article)
        assert fact["tier"] == 2
        assert fact["summary"] == ""  # tier2 → summary 생략 (토큰 절감)
