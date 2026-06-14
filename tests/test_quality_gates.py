"""품질 게이트 단위 테스트 — check-coverage-gaps · resolve-refs (하이픈 파일명 → importlib 로드)"""
import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ccg = _load("ccg", "scripts/newsletter/check-coverage-gaps.py")
rr = _load("rr", "scripts/newsletter/resolve-refs.py")


class TestCoverageGaps:
    def test_covered_by_keyword(self):
        assert ccg._covered("두산로보틱스 협동로봇 매출 감소", "두산로보틱스의 매출이 줄었다.")

    def test_not_covered(self):
        assert not ccg._covered("Figure AI 리테일 계약", "협동로봇 시장이 정체다.")

    def test_empty_summary_not_covered(self):
        assert not ccg._covered("아무 헤드라인", "")

    def test_headline_words_korean_english(self):
        words = ccg._headline_words("두산로보틱스 Figure 계약 체결")
        assert "두산로보틱스" in words
        assert "figure" in words
        # 2자 한글·3자 영문은 제외
        assert "계약" not in words

    def test_summary_field_read(self):
        """briefing 스키마 필드는 summary — text 만 읽던 회귀 방지 (필드명 버그 재발 방지)"""
        import inspect
        src = inspect.getsource(ccg.main)
        assert 'get("summary"' in src


class TestResolveRefs:
    def _facts(self):
        return {
            "categories": [
                {"category_id": "cobots", "facts": [
                    {"id": "abc123", "title": "Doosan cobot revenue drops sharply",
                     "source_url": "https://x.com/1", "source_name": "RoboNews",
                     "source_date": "2026-06-10"},
                ]},
            ],
            "competitor_articles": {},
        }

    def test_build_index(self):
        by_id, articles = rr.build_index(self._facts())
        assert "abc123" in by_id
        assert len(articles) == 1

    def test_fill_from_joins_metadata(self):
        by_id, _ = rr.build_index(self._facts())
        entry = {"ref": "abc123", "url": ""}
        rr.fill_from(entry, by_id["abc123"])
        assert entry["url"] == "https://x.com/1"

    def test_title_fallback_token_match(self):
        _, articles = rr.build_index(self._facts())
        entry = {"title": "Doosan cobot revenue drops", "url": ""}
        assert rr.title_fallback(entry, articles) is not None

    def test_title_fallback_no_match(self):
        _, articles = rr.build_index(self._facts())
        entry = {"title": "Totally unrelated quantum computing news", "url": ""}
        assert rr.title_fallback(entry, articles) is None

    def test_unresolved_ratio_gate_exists(self):
        """미해결 >30% → exit 2 게이트 회귀 방지"""
        import inspect
        src = inspect.getsource(rr.main)
        assert "0.3" in src and "sys.exit(2)" in src
