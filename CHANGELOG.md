# Changelog

## 0.4.0 — 재현율 우선 분류 게이트 + 이중언어 키워드

좁은 도메인팩 키워드 탓에 **진짜 업계 기사가 점수 미달로 조용히 탈락**하던 문제 해결
("누락 없는 모니터링" 철학과 모순됐음). 키워드 점수는 이제 *비중(tier)* 만 정하고,
*포함 여부* 는 가르지 않는다.

### 변경
- **재현율 우선 게이트**(`classify.gate_decision`): 조직 카테고리에 **진짜 매칭된** 기사는
  `relevance < 2` 여도 `exclude` 하지 않고 `manual_review`(=tier2 헤드라인)로 살린다.
  살아남은 tier2 의 비중은 Haiku 중요도 채점(0.2.0)이 재정하고, 잡음은 낮게 매겨 각주行으로
  내린다 — 재현율은 게이트가, 정밀도는 LLM이 담당. 미분류 잡음만 종전대로 제외.
- **`setup-bootstrap` 에이전트**: 카테고리 `watch_keywords`·`keywords.yaml boost` 를
  **한국어+영어 브로드 어휘**로 넓게 생성하도록 지시 추가 — 새 도메인팩이 니치 전문용어만
  담아 진짜 기사를 놓치는 근본 원인 차단.
- 게이트 단위 테스트(`TestGateDecision`) 4건. 총 117.

## 0.3.1 — PR 클리핑 수집창 override

### 추가
- **`/pr-clipping [date] [hours]`**: 선택적 시간창 인자. 매일 돌리지 않는 경우
  수집창을 직접 넓힐 수 있다(예: 주 1회 `168`). 생략 시 기존 정책 그대로 —
  평일 24h, **월요일 72h(주말 자동 포함)**. `pr`·`pr-monitor` 서브커맨드 둘 다
  positional `hours` 수용(newsletter `[date] [hours]` 와 동일 UX).
- CLI 인자 테스트 2개(`TestCliArgs`). 총 113개.

## 0.3.0 — 코드 리뷰 안전 정리

심층 코드 리뷰의 저위험·고가치 지적을 반영. 동작 변화 없음(전부 구조·정리),
단위 테스트 111개 유지.

### 변경
- **공통 에러 베이스** `PrMonitorError`(`prmonitor/__init__.py`) 도입 —
  `BootstrapError`·`DomainPackError` 가 이를 상속. `except PrMonitorError` 로
  플러그인발 실패를 한 번에 잡을 수 있다(RuntimeError 호환 유지).
- **합성 모델 정책 상수화**(`steps/newsletter.py`): 흩어진 `"claude-sonnet-4-6"`
  리터럴을 `_SYNTH_MODEL_DEFAULT`(+`PRM_SYNTH_MODEL_DEFAULT` env)·
  `_BLOCKED_MODEL_SUBSTR` 한 곳으로. 모델 세대 교체 시 상수만 수정.
- **init venv 실패 안내 명시화**(`steps/init.py`): 조용히 끝내지 않고 원인+해결책
  (Python 설치)·세션은 계속 가능함을 stderr 로 안내.
- **`.gitignore`**: 번들 도메인팩 예시를 `config/examples/` 로 추적 가능하게 예외 추가.
- **dev 의존성 분리**: `pytest` 를 `requirements.txt`(런타임 venv 설치 대상)에서
  빼 `requirements-dev.txt` 로. 플러그인 런타임 venv 가 테스트 전용 패키지를 더는
  깔지 않는다. 테스트: `pip install -r requirements-dev.txt`.

### 정리
- step 모듈(`pre`·`post`·`newsletter`·`pr_monitor`)의 박제된 `.sh` 원본 라인번호
  주석 제거 — 참조 대상 bash 파일은 패키징에 없어 혼란만 줬다. 서술적 파일명
  참조는 보존. (`__version__` 0.1.0→0.3.0 동기화.)

## 0.2.0 — Haiku 기사 보강 + 합성 품질

키워드 점수만으로는 편집 중요도를 못 잡던 문제(묘기 기사가 실속 기사를 tier1에서
밀어냄)를 Haiku 보강 단계로 해결. 합성 인사이트의 억지 연결·할일형 함의·관찰 오염도
스펙으로 차단. 모든 변경은 **엔진(도메인 중립)** — 도메인팩만 구성하면 어느 산업이든
동일 품질. 단위 테스트 111개 통과.

### 추가
- **Step 5b 기사 보강** (`scripts/pipeline/enrich-articles.py`): classify→aggregate
  사이에서 Haiku 배치 1콜로 기사당 `{importance 1~5, 1줄요약}` 생성. 루브릭은
  도메인 중립 — 산업명은 `company-profile.company.industry`, 요약 언어는 `style.language`
  에서 주입. 도메인팩 `classify-tuning.importance_hint`(선택)로 산업별 예시 보강 가능.
  비치명적(실패 시 키워드 점수 폴백)·idempotent.
- `aggregate.tier_score()`: Haiku importance 우선, 없으면 relevance_score 폴백.
- 보강 기사 단위 테스트(`TestImportanceTiering`) + ko_summary override 테스트.

### 변경
- **tier 배정**: 보강 기사는 `importance>=4` 만 tier1. 경쟁사명 자동승격(묘기 기사
  tier1 유입 원인) 제거. 키워드 점수는 폴백·카테고리 분류·boost/exclude 용으로 유지.
- **summary**: enrich `ko_summary` 우선 사용 — 추출 실패/영문 summary 문제 해결,
  tier2 헤드라인에도 한국어 내용 제공.
- **합성 산출 경로**: `newsletter-briefing-*.json`·enrich 출력을 워크스페이스
  (`paths.BRIEFING_DIR`)로 이동 — `claude -p` 가 `.claude/` 캐시를 민감파일로 분류해
  Write 를 차단하던 문제 해결.
- **headless thinking 비활성** (`MAX_THINKING_TOKENS=0`): 합성은 구조적 생성이라
  extended thinking 이 품질을 못 사주면서 rate-limit 시 9분 폭주를 유발 → 제거(4분).
- **insight-synthesizer 스펙**: ①관찰은 외부 기사만(self-context 금지) ②한 인사이트
  = 하나의 공통 줄기(시간적 우연 묶기 금지) ③할일형 함의("확인 대상" 등) 금지 →
  판단으로 종결 ④tier2 전 기사를 headlines 에 1줄로 포함.
- **format sweep**(`이 밖에`): 건수 대신 한국어 제목 표시(영문·짧은 조각 필터).

### 수정
- stale 테스트 정리: `samsung_boost`→`stakeholder_boost` 이름 변경 반영,
  format 문단 마진 상수(16px) 테스트 동기화.

## 0.1.0 — Plugin + engine/domain-pack refactor

`pr-monitor`(단일 워크스페이스 뉴스 도구)를 **공식 Claude Code 플러그인** +
**도메인 무지 엔진 + 조직별 도메인팩**으로 재구성. 산업에 무관한 범용 뉴스 모니터로,
배포본 예시 도메인팩 = **Contoso Motors(EV)** 를 번들. Windows·macOS Claude Code Desktop 지원.

### 추가
- **3-루트 경로 모델** (`prmonitor/paths.py`): `${CLAUDE_PLUGIN_ROOT}`(로직)·`${CLAUDE_PROJECT_DIR}`(도메인팩·산출물)·`${CLAUDE_PLUGIN_DATA}`(venv·캐시). 비플러그인 dev 폴백 포함.
- **크로스플랫폼 Python CLI** (`python -m prmonitor <pre|post|pr|newsletter|init|paths>`): bash 오케스트레이터 5개 + `common.sh` 대체. Windows venv 부트스트랩(`Scripts/python.exe`, `py` 런처).
- **플러그인 패키징**: `.claude-plugin/plugin.json`(+userConfig 시크릿)·`marketplace.json`·`hooks/hooks.json`(SessionStart 초기화).
- **도메인팩 로더** (`prmonitor/domainpack.py`) + 13개 팩 YAML(`config-templates/`): categories·style·classify-tuning·tone-lexicon·media·branding·pr-queries·prompt-examples 등.
- **`/setup` 도메인팩 생성 마법사** + 루틴 등록 안내.
- 신규 엔진 레이어 단위 테스트 9개 (`tests/test_prmonitor.py`).

### 변경
- 시크릿(Azure)을 `delivery.yaml` 평문 → 플러그인 `userConfig`→OS 키체인. `send-email.py` 가 env 우선.
- 하드코딩 도메인 상수(카테고리 색/라벨·톤 블랙리스트·분류 튜닝·매체명·브랜딩)를 코드 → 도메인팩 YAML 로 외부화. 엔진은 config 를 읽어 동작.
- 커맨드·루틴이 bash 대신 Python CLI 호출. 하드코딩 절대경로 제거.

### 한계 (설계상)
- `prompt-examples.yaml`(합성 few-shot 판단 예시)는 자동 생성 불가 — 새 조직은 운영하며 직접 큐레이션.
- Cowork(클라우드) 미지원 — 수집이 차단됨. 로컬 데스크탑 전용.

### 검증
- pytest 107 통과(기존 98 회귀 + 신규 9). 엔진이 도메인팩에서 값을 읽음을 증명(`format._TONE_BLACKLIST` ← `style.yaml`).
- Architect 리뷰: Phase 0–2 견고, 포팅 충실, 시크릿 처리 정확. 잔여 하드코딩(branding 등) 수정 반영.
