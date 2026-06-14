---
name: briefing-formatter
description: insight-synthesizer JSON을 받아 산업군 인사이트 뉴스레터 HTML을 생성한다. LLM 호출 없이 결정론적 파이썬 템플릿으로 작동. CSS class 기반 HTML 출력.
---

# Briefing Formatter Skill (v3)

insight-synthesizer JSON → 산업군 인사이트 뉴스레터 HTML.

**LLM 호출 없음.** 결정론적 파이썬 템플릿. 재현성 100%.

운영 경로에서는 `scripts/newsletter/run-post.sh` 가 자동 호출한다 (resolve-refs 조인 → 렌더 →
품질 게이트). 이 스킬을 직접 호출하는 건 디버깅·재렌더 때다.

## 사용법

```bash
python3 .claude/skills/briefing-formatter/format.py \
    --input  data/processed/newsletter-briefing-2026-06-12.json \
    --date   2026-06-12 \
    --output data/output/newsletter/newsletter-report-2026-06-12.html \
    --collection-start 2026-06-10 \
    --foreign 42 --domestic 24 \
    --hours 48 \
    --facts data/processed/newsletter-facts-2026-06-12.json
```

## CLI 인수

| 인수 | 필수 | 설명 |
|------|------|------|
| `--input` | ✅ | insight-synthesizer JSON 경로 |
| `--date` | ✅ | 발행일 YYYY-MM-DD |
| `--output` | ✅ | 출력 HTML 경로 |
| `--collection-start` | 선택 | 수집 시작일 YYYY-MM-DD |
| `--foreign` / `--domestic` | 선택 | 해외/국내 기사 수 (기본 0) |
| `--hours` | 선택 | 수집 시간창 — 일간(≤24)/주간 헤드라인 분량 판단 (기본 24) |
| `--facts` | 선택 | newsletter-facts JSON — 출처 목록 결정론 백필 (LLM 누락 방지) |

## 입력 JSON 스키마

**스키마 정본은 `.claude/agents/insight-synthesizer.md` 의 "JSON 출력 스키마" 섹션이다** —
여기 복사해 두지 않는다 (사본 드리프트 방지). 핵심만:

- 출처는 `ref` id 로만 — `resolve-refs.py` 가 렌더 전에 URL·매체명·날짜를 조인해 둔 상태를 기대
- `all_sources`·`coverage_table` 은 폐지 — `--facts` 백필이 출처 목록을 보장
- 구 필드명(`supporting_facts`, `facts[].url` 등)은 fallback 으로 읽힌다 (정규화 내장)

## 렌더 외 부수 동작

- **품질 검증**: 톤 블랙리스트·문장 길이·비약 패턴 검사 → 경고를 stdout +
  `.quality-warnings-{date}.json` (run-post.sh 발송 게이트가 읽음)
- **커버리지 보완**: 카테고리 요약에 누락된 헤드라인을 "이 밖에 … 등도 주목됐다" 로 자동 추가
- **출처 백필**: `--facts` 의 기사 중 본문에 등장하지 않은 것도 출처 목록에 등록

## 카테고리 ID → 색상

| category_id | 한국어 | 색상 |
|-------------|--------|------|
| `cobots` | 협동로봇 | `#2563eb` (파랑) |
| `humanoid` | Humanoid | `#7c3aed` (보라) |
| `amr` | AMR | `#d97706` (주황) |
| `manufacturing_platform` | 제조 자동화 플랫폼 | `#059669` (초록) |
| `other_industrial` | 기타산업 | `#dc2626` (빨강) |
| `funding` | 투자·M&A | `#6b7280` (회색) |
| `industrial_robot` | (legacy → other_industrial) | `#dc2626` |

## 출력 구조 (format.py v3)

```
CONTOSO MOTORS · INDUSTRY INTELLIGENCE
산업군 인사이트 뉴스레터
YYYY년 MM월 DD일 (요일) · 수집기간 · N건 (해외 X, 국내 Y)

[TL;DR]
[경쟁사 동향 — 회사별 카드]
[이번 호 등장 기업 — 비주류·신생 1줄 설명, 있을 때만]
[인사이트 최대 3개 — 관찰(인라인 출처 [n]) / 자사 함의]
[카테고리별 동향 — 요약 + 헤드라인 통합]
[출처 전체 목록]
```
