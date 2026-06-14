"""pytest 설정 — sys.path + 공유 픽스처.

테스트는 배포본 config-templates(범용 예시)가 아니라 tests/fixtures/config 의
로보틱스 테스트 데이터를 읽는다. 그래야 배포 기본값을 범용으로 바꿔도 테스트가
영향받지 않는다. domainpack 이 CONFIG_DIR(=CLAUDE_PROJECT_DIR/config)을 먼저 보므로
CLAUDE_PROJECT_DIR 을 fixtures 로 고정한다 (prmonitor.paths import 전에 설정).
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 엔진이 fixtures/config(로보틱스 테스트 데이터)를 도메인팩으로 읽게 한다.
os.environ.setdefault("CLAUDE_PROJECT_DIR", str(PROJECT_ROOT / "tests" / "fixtures"))

# scripts/ 아래 모듈 직접 import 가능하게
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import pytest


# ── 샘플 company profile (classify/aggregate 테스트 공용) ──────
@pytest.fixture
def sample_profile():
    return {
        "company": {
            "name": "에이콤로보틱스",
        },
        "categories": {
            "humanoid": {
                "label_ko": "Humanoid",
                "watch_keywords": ["휴머노이드", "humanoid", "이족보행"],
                "key_players": ["Boston Dynamics", "Figure AI"],
            },
            "cobots": {
                "label_ko": "협동로봇",
                "watch_keywords": ["협동로봇", "cobot", "협동"],
                "key_players": ["Universal Robots", "두산로보틱스"],
            },
            "amr": {
                "label_ko": "AMR",
                "watch_keywords": ["AMR", "자율주행", "AGV"],
                "key_players": ["티로보틱스", "Omron"],
            },
            "manufacturing_platform": {
                "label_ko": "제조 자동화 플랫폼",
                "watch_keywords": ["스마트팩토리", "MES", "제조 AI"],
                "key_players": ["Siemens", "Rockwell"],
            },
            "other_industrial": {
                "label_ko": "기타산업",
                "watch_keywords": ["산업용로봇", "FANUC", "KUKA"],
                "key_players": ["ABB", "Yaskawa"],
            },
            "funding": {
                "label_ko": "투자·M&A",
                "watch_keywords": ["투자", "M&A", "자금조달", "시리즈"],
                "key_players": [],
            },
        },
        "competitors": [
            {"name": "두산로보틱스", "aliases": ["Doosan Robotics", "두산"]},
            {"name": "FANUC", "aliases": []},
            {"name": "ABB", "aliases": []},
        ],
    }


# ── 샘플 기사 리스트 (aggregate 테스트용) ──────────────────────
@pytest.fixture
def sample_articles():
    return [
        {
            "title": "두산로보틱스 협동로봇 매출 반토막…AI 전환 가속",
            "relevance_score": 5,
            "published_date": "2026-06-10",
            "competitors_mentioned": ["두산로보틱스"],
            "url": "https://example.com/1",
            "categories": ["cobots"],
            "source_name": "한국경제TV",
            "word_count": 800,
            "first_paragraph": "두산로보틱스 협동로봇 매출이 반토막 났다.",
        },
        {
            "title": "두산로보틱스 협동로봇 매출 급감…실적 발표",
            "relevance_score": 4,
            "published_date": "2026-06-09",
            "competitors_mentioned": ["두산로보틱스"],
            "url": "https://example.com/2",
            "categories": ["cobots"],
            "source_name": "로봇신문",
            "word_count": 600,
            "first_paragraph": "두산로보틱스 실적 악화가 지속되고 있다.",
        },
        {
            "title": "FANUC AI 로봇 1000대 현장 배치",
            "relevance_score": 5,
            "published_date": "2026-06-11",
            "competitors_mentioned": ["FANUC"],
            "url": "https://example.com/3",
            "categories": ["other_industrial"],
            "source_name": "Machinery",
            "word_count": 1200,
            "first_paragraph": "FANUC이 AI 로봇을 1000대 배치했다.",
        },
        {
            "title": "NVIDIA, 로봇 파운데이션 모델 Cosmos 3 공개",
            "relevance_score": 3,
            "published_date": "2026-06-08",
            "competitors_mentioned": [],
            "url": "https://example.com/4",
            "categories": ["humanoid"],
            "source_name": "The Robot Report",
            "word_count": 400,
            "first_paragraph": "NVIDIA가 Cosmos 3를 공개했다.",
        },
        {
            "title": "두산 협동로봇, AI 디버링 MOU 체결",
            "relevance_score": 2,
            "published_date": "2026-06-07",
            "competitors_mentioned": ["두산로보틱스"],
            "url": "https://example.com/5",
            "categories": ["cobots"],
            "source_name": "세계일보",
            "word_count": 300,
            "first_paragraph": "두산이 AI 디버링 협동로봇 MOU를 체결했다.",
        },
        {
            "title": "KUKA 신형 산업용 로봇 출시",
            "relevance_score": 2,
            "published_date": "2026-06-06",
            "competitors_mentioned": [],
            "url": "https://example.com/6",
            "categories": ["other_industrial"],
            "source_name": "Robotics Today",
            "word_count": 700,
            "first_paragraph": "KUKA가 신형 로봇을 출시했다.",
        },
    ]


# ── 샘플 briefing JSON (format.py 테스트용) ────────────────────
@pytest.fixture
def sample_briefing():
    return {
        "date": "2026-06-11",
        "tldr": "FANUC AI 로봇 1000대 배치, 두산 협동로봇 매출 반토막 등 로봇 산업 구조 전환 관찰.",
        "headlines": [
            {
                "text": "FANUC AI 로봇 1000대 현장 배치",
                "source": "Machinery",
                "group": "FANUC",
                "url": "https://example.com/h1",
            },
            {
                "text": "두산로보틱스 협동로봇 매출 반토막",
                "source": "한국경제TV",
                "group": "두산로보틱스",
                "url": "https://example.com/h2",
            },
        ],
        "insights": [
            {
                "title": "협동로봇 시장 구조적 둔화 — 자사 중소 고객 실적 확인 시점",
                "observation": (
                    "두산로보틱스 협동로봇 매출이 389억원에서 199억원으로 급감했다. "
                    "FANUC은 AI 기반 산업용 로봇 1000대를 현장 배치했다."
                ),
                "implication": (
                    "두산 협동로봇 매출 반토막은 협동로봇 수요 둔화가 구조적일 수 있음을 시사한다. "
                    "자사 중소 고객도 유사한 압력권에 있을 가능성을 확인해야 한다."
                ),
                "facts": [
                    {
                        "text": "두산로보틱스 협동로봇 매출 389억→199억",
                        "source_name": "한국경제TV",
                        "source_date": "2026-06-10",
                        "source_url": "https://example.com/f1",
                    },
                    {
                        "text": "FANUC AI 로봇 1000대 배치",
                        "source_name": "Machinery",
                        "source_date": "2026-06-11",
                        "source_url": "https://example.com/f2",
                    },
                ],
            },
        ],
        "category_summary": [
            {
                "category_id": "cobots",
                "category_name": "협동로봇",
                "color": "#2563eb",
                "summary": "두산 매출 급감으로 협동로봇 시장 위축 신호가 감지된다.",
                "sources": [
                    {
                        "name": "한국경제TV",
                        "date": "2026-06-10",
                        "url": "https://example.com/s1",
                    }
                ],
            },
            {
                "category_id": "other_industrial",
                "category_name": "기타산업",
                "color": "#dc2626",
                "summary": "FANUC AI 로봇 1000대 배치는 산업용 로봇의 AI 전환을 시사한다.",
                "sources": [
                    {
                        "name": "Machinery",
                        "date": "2026-06-11",
                        "url": "https://example.com/s2",
                    }
                ],
            },
        ],
        "all_sources": [
            {
                "url": "https://example.com/h1",
                "name": "Machinery",
                "date": "2026-06-11",
                "title": "FANUC AI 로봇 1000대 배치",
                "summary": "FANUC이 AI 로봇을 대량 배치했다",
                "category": "other_industrial",
            },
            {
                "url": "https://example.com/h2",
                "name": "한국경제TV",
                "date": "2026-06-10",
                "title": "두산로보틱스 협동로봇 매출 반토막",
                "summary": "두산로보틱스 실적 악화",
                "category": "cobots",
            },
            {
                "url": "https://example.com/f1",
                "name": "한국경제TV",
                "date": "2026-06-10",
                "title": "두산 협동로봇 매출 급감",
                "summary": "협동로봇 시장 위축",
                "category": "cobots",
            },
            {
                "url": "https://example.com/f2",
                "name": "Machinery",
                "date": "2026-06-11",
                "title": "FANUC AI 로봇 1000대",
                "summary": "AI 로봇 대량 배치",
                "category": "other_industrial",
            },
            {
                "url": "https://example.com/s1",
                "name": "한국경제TV",
                "date": "2026-06-10",
                "title": "두산로보틱스, 로봇팔 매출 반토막",
                "summary": "실적 발표",
                "category": "cobots",
            },
            {
                "url": "https://example.com/s2",
                "name": "Machinery",
                "date": "2026-06-11",
                "title": "FANUC AI 로봇 1000대 배치",
                "summary": "AI 로봇 배치",
                "category": "other_industrial",
            },
        ],
        "coverage_table": {
            "cobots": 2,
            "humanoid": 0,
            "amr": 0,
            "manufacturing_platform": 0,
            "other_industrial": 1,
            "funding": 0,
        },
    }
