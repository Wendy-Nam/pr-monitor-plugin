"""format.py (briefing-formatter) 스모크 테스트.

HTML 생성, 품질 검증 게이트, SourceRegistry.
format.py는 scripts/ 패키지 밖에 있어 importlib으로 로드.
"""
# pylint: disable=redefined-outer-name,import-outside-toplevel

import importlib.util
from pathlib import Path

# ── format.py 모듈 로드 (scripts/ 밖 별도 위치) ────────────────
_FORMAT_PATH = Path(__file__).resolve().parent.parent / "skills/briefing-formatter/format.py"
_spec = importlib.util.spec_from_file_location("briefing_format", _FORMAT_PATH)
fmt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fmt)


class TestValidateBriefingQuality:
    def test_clean_text_no_warnings(self):
        data = {
            "tldr": "테스트 TL;DR입니다. 핵심 팩트를 요약한다.",
            "insights": [],
        }
        warnings = fmt.validate_briefing_quality(data)
        assert len(warnings) == 0

    def test_blacklist_word_detected(self):
        data = {
            "tldr": "이번 주 모멘텀이 확보됐다.",
            "insights": [],
        }
        warnings = fmt.validate_briefing_quality(data)
        assert any("모멘텀" in w for w in warnings)

    def test_blacklist_word_signal_detected(self):
        data = {
            "tldr": "이번 움직임은 강한 시그널이다.",
            "insights": [],
        }
        warnings = fmt.validate_briefing_quality(data)
        assert any("시그널" in w for w in warnings)

    def test_banned_ending_detected(self):
        data = {
            "tldr": "정리하자면 확인이 필요하다.",
            "insights": [
                {"title": "테스트", "implication": "추가 확인 필요", "observation": ""}
            ],
        }
        warnings = fmt.validate_briefing_quality(data)
        assert any("확인 필요" in w for w in warnings)

    def test_insight_blacklist(self):
        data = {
            "tldr": "",
            "insights": [
                {"title": "이번 움직임은 전환점이다", "implication": "", "observation": ""}
            ],
        }
        warnings = fmt.validate_briefing_quality(data)
        assert any("전환점" in w for w in warnings)

    def test_empty_insights_no_error(self):
        data = {"tldr": "", "insights": []}
        warnings = fmt.validate_briefing_quality(data)
        assert len(warnings) == 0


class TestBuildHtml:
    def test_creates_valid_html(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11", hours=24)
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html

    def test_includes_brand(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "ACME ROBOTICS" in html

    def test_includes_meta_date(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "2026" in html

    def test_includes_tldr_section(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "TL;DR" in html or "tldr" in html.lower()

    def test_includes_insight_section(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "INSIGHT" in html or "insight" in html.lower()

    def test_includes_source_section(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "출처" in html or "source" in html.lower()

    def test_includes_competitor_section(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "경쟁사" in html

    def test_headlines_rendered(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "FANUC AI 로봇" in html

    def test_insight_content_rendered(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "협동로봇" in html
        assert "389억" in html


class TestInsightFactTitle:
    def test_strips_implication_after_dash(self):
        # '[팩트] — [자사 함의]' → 팩트만 (함의는 자사 함의 블록에 중복되므로)
        out = fmt.insight_fact_title(
            "NVIDIA·Amazon이 유럽 휴머노이드에 $1.4B 투자 — 자사 포지셔닝 검토 시점"
        )
        assert out == "NVIDIA·Amazon이 유럽 휴머노이드에 $1.4B 투자"

    def test_keeps_title_without_dash(self):
        out = fmt.insight_fact_title("협동로봇 시장 구조적 둔화")
        assert out == "협동로봇 시장 구조적 둔화"

    def test_tldr_fallback_paragraph_before_jasa(self):
        # LLM이 빈 줄 없이 쓴 TL;DR — "자사~" 문장 앞에서 자동 문단 분리
        out = fmt.render_tldr("NVIDIA가 플랫폼을 공개했다. NEURA가 투자를 유치했다. 자사는 편입 여부가 확인 대상이다.")
        assert out.count('class="tldr-p"') == 2
        head, tail = out.split("자사는", 1)
        assert 'tldr-p' in head  # 자사 문장은 두 번째 문단에

    def test_tldr_explicit_paragraphs_kept(self):
        out = fmt.render_tldr("팩트 문단.\n\n자사 함의 문단.")
        assert out.count('class="tldr-p"') == 2

    def test_refs_content_matched_not_positional(self):
        # fact 순서와 서술 순서가 어긋나도 내용 매칭으로 올바른 문장에 번호
        # (인사이트 관찰에서 BD 문장에 LG 기사 번호가 붙던 버그)
        class _Reg:
            def get_num(self, url):
                return {"https://x.com/lg": 1, "https://x.com/bd": 2}.get(url)
        out = fmt.attach_inline_refs(
            "보스턴다이나믹스가 아키텍처를 발표했다. LG CNS는 물류창고 도입을 추진한다.",
            [{"url": "https://x.com/lg", "title": "LG CNS, 물류창고 휴머노이드 도입 추진"},
             {"url": "https://x.com/bd", "title": "보스턴다이나믹스 아키텍처 재정의"}],
            _Reg())
        bd_sent_end = out.index("발표했다.")
        lg_sent_start = out.index("LG CNS는")
        assert "[2]" in out[bd_sent_end:lg_sent_start]   # BD 문장 → BD 기사 [2]
        assert "[1]" in out[lg_sent_start:]               # LG 문장 → LG 기사 [1]

    def test_paragraph_break_preserved_in_summary(self):
        # 카테고리 요약의 빈 줄 문단 구분 — 좁은 마진(6px)의 새 <p>로 보존
        class _FakeReg:
            def get_num(self, url): return None
        out = fmt.attach_inline_refs(
            "삼성전자가 투자했다.\n\nLG는 훈련소를 짓는다.", [], _FakeReg())
        assert 'margin:6px 0 0;' in out
        assert "<br><br>" not in out

    def test_stub_head_keeps_full_title(self):
        # 대시 앞이 뉴스 스텁이면 자르지 않고 전체 노출
        # ("NEURA $1.4B" 만 남아 인사이트가 아닌 라벨이 되는 회귀 방지)
        out = fmt.insight_fact_title("NEURA $1.4B — 물리적 AI 플랫폼에 자본이 집중되고 있다")
        assert "자본이 집중되고 있다" in out

    def test_implication_not_in_rendered_title(self, sample_briefing):
        # 렌더된 인사이트 헤더(제목)에 '—' 뒤 함의 문구가 들어가지 않아야 함
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "자사 중소 고객 실적 확인 시점" not in html.split("자사 함의")[0]

    def test_empty_tldr_still_works(self):
        data = {
            "date": "2026-06-11",
            "tldr": "",
            "headlines": [],
            "insights": [],
            "category_summary": [],
            "all_sources": [],
            "coverage_table": {
                "cobots": 0,
                "humanoid": 0,
                "amr": 0,
                "manufacturing_platform": 0,
                "other_industrial": 0,
                "funding": 0,
            },
        }
        html = fmt.build_html(data, "2026-06-11")
        assert "<!DOCTYPE html>" in html

    def test_collection_start_shown(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11", collection_start="2026-06-08")
        assert "수집 기간" in html or "2026-06-08" in html


class TestCompanyGlossary:
    def test_renders_glossary_box(self, sample_briefing):
        data = dict(sample_briefing)
        data["company_glossary"] = [
            {"name": "NEURA Robotics", "desc": "독일 휴머노이드 스타트업"},
        ]
        html = fmt.build_html(data, "2026-06-11")
        assert "이번 호 등장 기업" in html
        assert "NEURA Robotics" in html
        assert "독일 휴머노이드 스타트업" in html

    def test_no_glossary_no_box(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        assert "이번 호 등장 기업" not in html

    def test_glossary_sorted_by_country(self, sample_briefing):
        data = dict(sample_briefing)
        data["company_glossary"] = [
            {"name": "줌라이언", "desc": "한국 휴머노이드 로봇 개발사"},
            {"name": "Standard Bots", "desc": "미국 산업용 협동로봇 개발사"},
            {"name": "NEURA Robotics", "desc": "독일 휴머노이드 스타트업"},
        ]
        html = fmt.build_html(data, "2026-06-11")
        sec = html.split("이번 호 등장 기업")[-1]
        # 국가 첫 단어 정렬: 독일 → 미국 → 한국
        assert sec.index("NEURA") < sec.index("Standard Bots") < sec.index("줌라이언")

    def test_glossary_skips_incomplete_entry(self, sample_briefing):
        data = dict(sample_briefing)
        data["company_glossary"] = [{"name": "노설명", "desc": ""}]
        html = fmt.build_html(data, "2026-06-11")
        # name/desc 둘 다 있어야 렌더 — desc 없으면 박스 자체 미생성
        assert "이번 호 등장 기업" not in html


class TestCompetitorGroupValidation:
    def test_mislabeled_competitor_group_reassigned(self):
        # 헤드라인 텍스트에 그 회사명이 없으면 경쟁사 그룹 오배정 → 카테고리 재배정
        # (사례: 중국 휴머노이드 85% 제조 기사가 Tesla 그룹에 묶임)
        data = {
            "date": "2026-06-11",
            "tldr": "테스트",
            "headlines": [
                {"text": "중국, 세계 휴머노이드의 85% 저가 제조", "source": "Fortune",
                 "group": "Tesla", "url": "https://example.com/cn"},
            ],
            "insights": [],
            "category_summary": [],
            "all_sources": [
                {"url": "https://example.com/cn", "name": "Fortune",
                 "date": "2026-06-10", "title": "China makes 85% of humanoids",
                 "summary": "중국 휴머노이드 제조", "category": "humanoid"},
            ],
            "coverage_table": {},
        }
        html = fmt.build_html(data, "2026-06-11")
        # Tesla 그룹 헤더가 생기면 안 됨
        assert ">Tesla</div>" not in html

    def test_valid_competitor_group_kept(self, sample_briefing):
        html = fmt.build_html(sample_briefing, "2026-06-11")
        # 제목에 회사명 있는 경쟁사 그룹은 유지 + h2 섹션 서식
        assert "<h2>경쟁사 동향</h2>" in html
        assert ">FANUC</div>" in html


class TestCategoryOrder:
    def test_category_blocks_follow_canonical_order(self):
        # LLM 출력 순서와 무관하게 CAT_ORDER(cobots→humanoid→...→funding)로 고정
        data = {
            "date": "2026-06-11", "tldr": "테스트", "headlines": [], "insights": [],
            "category_summary": [
                {"category_id": "funding", "category_name": "투자·M&A", "summary": "에프.", "sources": []},
                {"category_id": "humanoid", "category_name": "Humanoid", "summary": "에이치.", "sources": []},
                {"category_id": "cobots", "category_name": "협동로봇", "summary": "씨.", "sources": []},
            ],
            "all_sources": [], "coverage_table": {},
        }
        html = fmt.build_html(data, "2026-06-11")
        sec = html.split("카테고리별 동향")[-1]
        # 주의: HTML 이스케이프로 M&A → M&amp;A
        assert sec.index("협동로봇") < sec.index("Humanoid") < sec.index("투자·M&amp;A")


class TestCategoryHeadlineMerge:
    def _minimal(self):
        return {
            "date": "2026-06-11",
            "tldr": "테스트",
            "headlines": [
                {"text": "유니트리 신형 휴머노이드 공개", "source": "로봇신문",
                 "group": "humanoid", "url": "https://example.com/h1"},
            ],
            "insights": [],
            "category_summary": [
                {"category_id": "humanoid", "category_name": "Humanoid",
                 "color": "#7c3aed", "summary": "휴머노이드 동향 요약.", "sources": []},
            ],
            "all_sources": [],
            "coverage_table": {},
        }

    def test_no_legacy_category_headline_section(self):
        html = fmt.build_html(self._minimal(), "2026-06-11")
        assert "카테고리별 헤드라인" not in html  # 구 섹션 제거됨
        assert "카테고리별 동향" in html

    def test_category_block_has_inline_refs_no_list(self):
        import re
        html = fmt.build_html(self._minimal(), "2026-06-11")
        cat = html.split("카테고리별 동향")[-1].split("<h2")[0]
        # 나열(li) 없이 서술에 인라인 번호주석 [n] 으로 링크
        assert "<li" not in cat
        assert re.search(r'\[\d+\]', cat)
        # 카테고리 기사 url 이 문서에 링크로 존재
        assert "example.com/h1" in html


class TestSourceRegistry:
    def test_register_and_get_num(self):
        reg = fmt.SourceRegistry()
        num = reg.register(
            url="https://example.com/a",
            name="테스트",
            date="2026-06-10",
            summary="요약",
            category="cobots",
        )
        assert num == 1
        assert reg.get_num("https://example.com/a") == 1

    def test_deduplicate_same_url(self):
        reg = fmt.SourceRegistry()
        num1 = reg.register("https://example.com/a", "A", "2026-06-10", "text", "cobots")
        num2 = reg.register("https://example.com/a", "A", "2026-06-10", "text", "cobots")
        assert num1 == num2
        assert len(reg.all_entries()) == 1

    def test_multiple_entries(self):
        reg = fmt.SourceRegistry()
        reg.register("https://example.com/1", "A", "2026-06-10", "text1", "cobots")
        reg.register("https://example.com/2", "B", "2026-06-11", "text2", "humanoid")
        assert len(reg.all_entries()) == 2

    def test_renumber_by_category(self):
        reg = fmt.SourceRegistry()
        reg.register("https://example.com/h1", "A", "2026-06-10", "text", "humanoid")
        reg.register("https://example.com/c1", "B", "2026-06-11", "text", "cobots")
        reg.renumber_by_category(["cobots", "humanoid"])
        entries = reg.all_entries()
        # cobots first (id=1), then humanoid (id=2)
        assert entries[0]["num"] == 1
        assert entries[0]["category"] == "cobots"
        assert entries[1]["num"] == 2
        assert entries[1]["category"] == "humanoid"

    def test_blog_domain_filtered(self):
        reg = fmt.SourceRegistry()
        num = reg.register(
            url="https://blog.naver.com/test",
            name="네이버 블로그",
            date="2026-06-10",
            summary="블로그 글",
            category="cobots",
            title="블로그",
        )
        assert num is None  # blog domains excluded
        assert len(reg.all_entries()) == 0

    def test_no_url_with_name_registered(self):
        reg = fmt.SourceRegistry()
        # URL 없지만 name 있으면 등록 (출처 목록에 이름만 표시)
        num = reg.register(url="#", name="이름만있는매체", date="", summary="", category="cobots")
        assert num == 1
        assert len(reg.all_entries()) == 1

    def test_fully_empty_skipped(self):
        reg = fmt.SourceRegistry()
        # name, date, title 모두 빈 경우 → skip
        num = reg.register(url="#", name="", date="", summary="", category="")
        assert num is None
