# Changelog

## 0.5.8 — venv 재실행 가드 핫픽스

0.5.7의 venv 재실행 픽스가 실제로 동작하지 않던 문제를 수정. (0.5.7 사용자는 0.5.8로 업데이트 권장.)

### 수정
- **재실행 가드 심볼릭 링크 오판 수정**: 0.5.7은 `Path(sys.executable).resolve() == venv_py.resolve()` 로 "이미 venv인지"를 판정했는데, venv의 `python3`가 시스템 바이너리로의 심볼릭 링크라 `.resolve()` 시 양쪽이 동일 실체 경로가 되어 **항상 참 → re-exec 건너뜀**. 결국 0.5.7 픽스가 무효였고 `ModuleNotFoundError: yaml` 가 그대로 재현됐다. 가드를 **인터프리터 prefix 비교**(`sys.prefix == <venv_dir>`)로 교체 — 실제로 venv 안에서 돌 때만 참이 된다. 시스템 파이썬에서 `post`·`newsletter` 완주 확인. ([#40](https://github.com/Wendy-Nam/pr-monitor-plugin/pull/40))

## 0.5.7 — venv 재실행 버그픽스

뉴스레터 합성이 시스템 파이썬 환경에서 깨지던 버그 수정. 엔진 동작·설정 스키마 변경 없음.

### 수정
- **venv 재실행(re-exec)**: 런처는 디스패처를 시스템 파이썬으로 띄운다(첫 실행엔 venv가 없으므로). 부트스트랩 후 프로세스를 venv 인터프리터로 re-exec 하도록 변경. in-process 설정 읽기(`domainpack`/PyYAML·`pipeline_cfg`)가 **시스템 파이썬에 PyYAML이 없을 때 `ModuleNotFoundError`로 죽던 문제** 해결 — 특히 뉴스레터 합성(`_synth_prompt`) 단계에서 발생. 서브프로세스 스텝은 이미 안전했고, 이제 부모 프로세스도 일관됨. idempotent(이미 venv면 no-op)·실 CLI 한정(`argv is None`)·Windows 안전(`subprocess`+exit code). ([#39](https://github.com/Wendy-Nam/pr-monitor-plugin/pull/39))

## 0.5.6 — 설정 튜닝 가이드(USAGE.md) + 번들 예시 보강

문서·예시 보강. 엔진 동작 변경 없음.

- **USAGE.md 신설**: 도메인팩 설정 & 품질 튜닝 가이드 — 설정이 작동하는 방식, 파일 지도, 품질 4대 레버(self-context·이중언어 키워드·boost/exclude·prompt-examples), 투자·M&A 라우팅(ma_route), 부트스트랩 깊이 기준.
- **README**: 커스터마이즈에 IMPORTANT 콜아웃 — "품질은 설정이 결정" + USAGE.md 링크 + 번들 예시 도메인팩이 깊이 본보기임을 안내.
- **번들 예시(config-templates) 보강**: classify-tuning 에 `ma_route` 예시 추가 — 새 조직이 투자·M&A 라우팅을 바로 보고 따라 쓰도록.
## 0.5.5 — Haiku 사건 클러스터링 + 순차 재시도 + M&A 라우팅

병렬 합성의 카테고리 중첩·누락·과병합을 한 번에 해결.

### 수정
- **Haiku 사건 클러스터링**: enrich(Haiku)가 기사마다 `cluster`(같은 사건 = 같은 정수)를 매기고, aggregate 가 cluster 로 대표 1건 병합. 표현이 달라도 같은 사건을 묶고(예: 다른 매체의 같은 인수설) **회사가 같아도 다른 사건은 안 묶는다**. 제목 char-유사도 휴리스틱의 과병합(무관 기사 손실) 제거.
- **순차 재시도**: 병렬 카테고리 digest 가 API 529 Overloaded 로 실패하면, 배치 후 부하 없는 상태에서 순차 재호출 → 카테고리 통째 누락 방지.
- **M&A·투자 라우팅**: 지분 인수/투자/매각은 제품 카테고리가 아니라 funding(투자·M&A)으로 (도메인팩 ma_route 설정 기반).
- **인사이트 제목**: ≤35자·한 절·거대 결론 비약 금지로 더 짧게.

### 알려진 한계
- 같은 사건 병합 시 현재 대표 1건의 출처만 표시 — 여러 매체 출처를 모두 다는 개선은 후속.
## 0.5.4 — 병렬 신뢰성 + 발송 게이트 + 도메인 중립화

v0.5.3 병렬 합성에서 카테고리가 누락되던 버그 수정 + 발송 규칙·도메인 하드코딩 정리.

### 수정
- **카테고리 누락 차단**: 병렬 digest 호출이 (1) 워크스페이스 Write 차단(`--add-dir` 추가), (2) API 529 Overloaded(백오프 재시도 3회)로 실패하면 그 카테고리가 통째로 드롭되던 문제 해결. tier2-only 카테고리(예: funding)도 digest 생성. 동시성 6 유지(빠름) + 일시 실패만 재시도.
- **발송 게이트**: 문장 길이(글자수 초과) 경고는 발송을 막지 않는다 — 가독성 참고일 뿐 사실 오류가 아니므로. 게이트는 날조·비약·금지어 등 내용 오류만 센다.
- **도메인 중립화**: 엔진의 도메인 특정 하드코딩 제거 — 카테고리 dot 색을 categories.yaml color(인라인)로 통일(특정 도메인 카테고리명 CSS 하드코딩 제거), resolve-refs STOP_TOKENS 를 도메인팩 generic_category_terms 에서 로드, 에이전트·프롬프트 예시를 도메인 중립 placeholder 로.
- 용어집은 회사 자체 개요만(이번 호 뉴스 제외), 인사이트 제목 ±40자·비약 금지.

## 0.5.3 — 합성 병렬 분리 + 동향 품질 (속도 ~4배·코드스위칭 0)

뉴스레터 합성을 '교차(인사이트) 1회 + 카테고리별 N회 병렬'로 분리. 속도 ~4배(약 6분→1.5분), 카테고리 동향의 흐름·번역·coverage 개선.

### 추가
- **병렬 분리 합성** (newsletter.py): 인사이트·tldr·glossary·landscape 는 교차 호출 1개, 카테고리 동향은 카테고리별 호출 N개를 동시 실행(최대 6) 후 머지. 각 호출이 자기 슬라이스만 보므로 빠르고 집중도↑. 기본 활성(`PRM_SYNTH_PARALLEL=0` 로 단일 폴백).
- **category-digest.md** 신설: 단일 카테고리 동향 전용 슬림 스펙(전 기사 커버·흐름·번역·micro-tail).

### 수정
- **stale-briefing 가드** (newsletter.py): 합성 직전 briefing 삭제 → 합성 실패 시 옛 briefing 이 조용히 렌더되던 버그 차단.
- **stale REVIEW_NEEDED.md** (post.py): 품질 게이트 통과 시 이전 보류서 제거 — 깨끗한 재실행이 헛경고 내던 문제 해소.
- **ref 폭탄 방지** (format.py): 문장당 inline 출처번호 상한(4) — 토큰 1개로 무관 기사가 한 문장에 쏠려 오귀속되던 문제 차단.
- **landscape 번들 경량화** (preload-synthesis-context.py): 합성 입력에서 archive(중복 누적 이력) 제외 → 노이즈 약 67%↓.
- **insight-synthesizer.md** 다이어트(462→~290줄) + 단일 스레드·코드스위칭 금지·동향 흐름 규칙.
- 합성 effort 기본값 medium (low 가 오히려 thrashing 해 더 느렸음).

## 0.5.2 — 카테고리 동향 렌더 수정 (raw ref·경쟁사 그룹핑)

뉴스레터 카테고리별 동향이 레퍼런스와 어긋나던 렌더 버그 수정 (format.py, 도메인 중립).

### 수정
- **raw 출처 id 누수 제거**: 합성기가 산문에 간혹 박는 `[a9f06e8]`(기사 id) 토큰을
  `attach_inline_refs` 가 제거. 출처번호 `[n]` 은 제목 매칭으로 별도로 붙으므로 raw id 는
  잉여·링크 안 걸리는 깨진 토큰이었다 — 이제 `[n]` 만 깔끔히 남는다.
- **경쟁사 그룹 역방향 승격**: 합성기가 `group:"기타"` 로 잘못 단 기사라도 제목이 등록
  경쟁사 별칭(예: Yaskawa·야스카와·야스카와전기)을 가리키면 그 경쟁사 카드로 승격
  (`competitor_for_text`). 기존엔 경쟁사→강등만 있고 기타→승격이 없어 진짜 경쟁사가
  '기타'에 갇혔다. 경쟁사 카드·카테고리 블록은 동일 `summary-block`(색 좌측 테두리)로 이미 통일.


## 0.5.1 — 도메인 중립성 정리 + setup 리셋 가드레일

### 변경
- **엔진 도메인 중립화**: `format.py` 토큰 매칭 stopword 에 박혀 있던 로보틱스 고유어
  (`로봇/robot/robotics`)를 제거하고 도메인팩 `generic_category_terms` 에서 가져오도록 변경 —
  어느 산업이든 그 도메인 범용어가 자동 반영. `self-context-updater`·`setup-bootstrap` 프롬프트의
  로봇·삼성·자사명 예시를 플레이스홀더로 교체(모델이 베껴 출력하던 부류 차단).
- **setup 리셋 가드레일**(`scripts/lib/setup-guard.py`): 기설정 워크스페이스에서 `/setup` 을
  잘못 실행해도 사용자 도메인팩을 말없이 덮지 않는다. `--check` 가 라이브 `company.name` 을
  번들 예시와 비교해 사용자 실데이터면 **exit 3**, `--backup` 이 `config.bak-<ts>/` 스냅샷 생성.
  INSTALL·setup-bootstrap 이 쓰기 전에 이 결정론적 가드를 거치도록 명문화(눈대중 금지).
  (init/SessionStart 은 이미 빈 파일만 시드해 안전.) 가드·중립화 테스트 추가 → 131.

## 0.5.0 — 이메일 발송 채널 추상화 (Azure 비종속)

이메일이 Microsoft Graph(Azure)에만 묶여 있어 Gmail·SES·사내 메일을 쓰는 조직은 발송을
못 쓰던 제약을 해소. `delivery.yaml` 의 `email.provider` 로 채널을 고른다.

### 추가
- **표준 SMTP provider**(`send-email.py`): `provider: smtp` 면 stdlib `smtplib` 로 발송 —
  Gmail·O365·AWS SES·사내 메일 등 표준 SMTP 서버 지원, **새 의존성 없음**. `email.smtp`
  블록(host·port·user·password·use_ssl), 시크릿은 env(userConfig→키체인) 우선.
- `send_mail`·`--validate` 가 provider 로 분기(`microsoft_graph` | `smtp`). 미지정 시
  `microsoft_graph` 기본 — **기존 설정 하위호환**.
- provider 라우팅·SMTP 파싱·에러 경로 테스트(`test_email_provider`) 5건.

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
- 게이트 단위 테스트(`TestGateDecision`) + **번들 도메인팩 스모크 테스트**(`test_bundled_packs`):
  모든 번들 팩이 파싱·스키마 충족하는지, 빈 워크스페이스(예시 팩만)에서 엔진 모듈이 크래시 없이
  import 되는지(과거 빈 `stakeholder_boosts` IndexError 부류 회귀 방지), `init` 시드 멱등·시크릿
  미시드까지 검증. `/setup` "예시 팩으로 시작" 케이스 안전망.
- **`/setup` 도메인팩 생성 3모드 정리**: 빠른 체험(번들 예시) + 우리 조직용 **자동 / 반자동 / 수동**.
  새 **반자동**(권장)은 사용자가 아는 항목(경쟁사·카테고리·키워드)을 **확정 사실로 고정**하고
  나머지만 리서치로 보충 — 자동의 속도 + 수동의 정확성 절충. `setup-bootstrap` 이 부분 입력을
  ground truth 로 다루도록 보강.

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
