---
name: setup
description: PR Monitor 설정 — 첫 설치(도메인팩 생성)·시크릿·수신자·키워드·루틴 등록·상태 확인. 리포트 생성은 /newsletter·/pr-clipping.
argument-hint: "[명령어] — 없으면 상태 + 미설정 시 설치 마법사"
---

# PR Monitor 설정

이 커맨드는 **설정·운영**만 담당한다(리포트 생성 X). 핵심은 **도메인팩 생성** — 이 엔진은 회사·산업을 모르고, `config/` 의 도메인팩 YAML 을 읽어 동작한다. 새 조직은 인터뷰로 도메인팩을 만든다.

## 경로 규칙 (플러그인 모델)

- 도메인팩·수신자·산출물 = **워크스페이스**(`${CLAUDE_PROJECT_DIR}/config`, `/data`). 사용자가 보고 편집·백업.
- 시크릿(Azure) = **플러그인 설정(userConfig) → OS 키체인**. YAML 평문 금지.
- 번들 기본값·도메인팩 1호 = 읽기전용 `${CLAUDE_PLUGIN_ROOT}/config-templates/`.
- 캐시·venv = `${CLAUDE_PLUGIN_DATA}` (숨김).

## 명령어 인식 (자연어 매칭)

| 입력 예 | 실행 |
|---|---|
| "상태", "설정 상태" | → **STATUS** |
| "설치", "초기 설정", "셋업", "새 조직" | → **INSTALL** (도메인팩 마법사) |
| "수신자 …" | → **RECIPIENTS** |
| "키워드 …" | → **KEYWORDS** |
| "Azure 키 …", "시크릿 …" | → **SECRETS** |
| "루틴 등록", "스케줄" | → **ROUTINES** |

없으면 STATUS 출력 후, 미설정(`.prmonitor-initialized` 없음 또는 `config/company-profile.yaml` 없음)이면 INSTALL 을 제안한다.

---

## STATUS

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" paths
```
로 해석된 경로 확인 후:

- 도메인팩: `config/company-profile.yaml` 존재 여부 + `company.name`.
- 시크릿: 환경변수 `CLAUDE_PLUGIN_OPTION_AZURE_CLIENT_SECRET` 또는 `config/delivery.yaml` 의 Azure 설정 여부(✅/❌만, 값은 절대 표시 X). 있으면 `send-email.py --validate` 로 실제 연결 확인.
- 수신자: `config/delivery.yaml` 그룹 수 + 파일럿 모드.
- 키워드: `config/keywords.yaml` boost/exclude 개수.
- 마지막 실행: `logs/executions/` 최신 파일 날짜.

```
━━━ PR Monitor 상태 ━━━
도메인팩: [company.name] / 카테고리 N개
Azure 인증: ✅ 연결됨 | ❌ 미설정 (→ "Azure 키" 로 설정)
수신자: N그룹 / 파일럿: ON|OFF
키워드: 부스트 N · 제외 N
마지막 실행: [날짜]
```

---

## INSTALL — 도메인팩 설치 마법사

`config/company-profile.yaml` 이 없거나 사용자가 설치를 요청하면 실행.

> [!CAUTION]
> **기존 파일 보호 (어떤 갈래든 쓰기 전에 먼저). 눈대중 말고 코드로 판정한다.**
> INSTALL·setup-bootstrap 은 절대 기존 도메인팩을 말없이 덮어쓰지 않는다. 쓰기 시작 전에:
> 1. **반드시 먼저 실행**한다 — 결정론적 가드:
>    ```bash
>    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/setup-guard.py" --check
>    ```
>    이건 라이브 `company.name` 을 번들 예시 템플릿과 비교해 판정한다.
>    - **exit 0** = 안전(미설정/예시 그대로) → 그대로 진행해도 됨.
>    - **exit 3** = **사용자 실데이터 감지** → 아래 2~3 없이는 한 글자도 쓰지 않는다.
> 2. exit 3 이면 STATUS 를 보여주고 "이미 [name] 도메인팩이 설정돼 있습니다. 새로 만들면
>    기존 설정을 잃습니다" 라고 알린 뒤 명시적으로 묻는다:
>    **(a) 백업 후 재설정 (b) 일부만 보강 (c) 취소**. 기본값은 **취소**.
> 3. (a)·(b) 승인 시 **쓰기 전에 백업을 먼저 뜬다**:
>    ```bash
>    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/setup-guard.py" --backup
>    ```
>    → `config.bak-<timestamp>/` 생성(삭제 아님). 이게 성공한 뒤에만 도메인팩 쓰기를 시작한다.
> 4. 개별 파일 `Write` 직전에도 대상이 이미 있으면 위 백업에 포함됐는지 확인하고 진행한다.
>    백업 없이 사용자 파일을 덮는 일은 없다.

먼저 어떻게 시작할지 고른다.

### 빠른 체험 — 번들 예시 팩으로 바로 시작
엔진 동작부터 보고 싶으면 `init` 이 시드해 둔 예시 도메인팩(콘토소 모터스·EV)으로 곧장 실행한다.
SECRETS + RECIPIENTS 만 안내. (우리 조직용은 아래에서 따로 만든다.)

### 우리 조직 도메인팩 만들기 — 세 모드 중 선택

| 모드 | 사용자 입력 | 에이전트가 하는 일 | 적합한 경우 |
|---|---|---|---|
| **자동** | 회사명 + 산업 (+선택 힌트) | 경쟁사·카테고리·키워드·소스를 **전부 웹 리서치**로 채움 | 빠르게 초안부터 받고 손보기 |
| **반자동** (권장) | 회사명·산업 + **아는 것 먼저**(경쟁사·카테고리·키워드·소스 일부) | 사용자 입력을 **확정 사실로 고정**, **빈 부분만 리서치로 보충** | 핵심은 내가 알고 나머지는 맡길 때 |
| **수동** | **전 항목 직접 입력** (인터뷰) | 리서치 없이 답만 받아 YAML 생성 | 완전히 직접 정의 / 폐쇄망·웹 불가 |

- **자동·반자동**: `setup-bootstrap` 서브에이전트(Agent 툴)를 호출한다. 회사명·산업(+반자동이면 사용자가 아는 항목)을 함께 넘긴다. 에이전트가 `${CLAUDE_PROJECT_DIR}/config/` 에 도메인팩 초안을 쓰고 **요약을 제시해 사람 승인**을 받는다(완전 자동 아님). 비용은 조직 생애 1회라 강한 모델 사용 정당.
- **수동**: 아래 인터뷰 항목을 한 번에 하나씩 묻고, 답으로 YAML 을 생성한다(서브에이전트·리서치 없음). 각 파일 스키마는 `config-templates/<name>.yaml` 본보기를 따른다.
- 공통 한계: `prompt-examples.yaml`(인사이트 few-shot)은 그 조직 실제 과거 브리핑이 있어야 해서 자동 생성되지 않는다 → 플레이스홀더만 깔고 큐레이션 필요를 안내.
- **반자동을 권하는 이유**: 사용자가 아는 경쟁사·카테고리가 가장 정확한 ground truth고, 리서치는 그 빈틈(영문 키워드·소스 URL·놓친 경쟁사)을 메우는 데 가장 값지다 — 자동의 속도 + 수동의 정확성을 절충한다.

수동 인터뷰 항목 → 생성 파일 (반자동에서 사용자가 미리 줄 수 있는 항목이기도 하다):

1. **회사·산업** (회사명·영문명·부서·한 줄 소개·경쟁사 목록) → `company-profile.yaml`, `branding.yaml`
2. **관심 카테고리** (id·한글라벨, 색은 기본 팔레트 자동) → `categories.yaml`
3. **키워드** (부스트·강제제외·일반제외) → `keywords.yaml`
4. **소스** (RSS·뉴스 검색쿼리·국가별) → `sources.yaml`
5. **언어·톤** (출력 언어·문장길이·금지어) → `style.yaml`
6. **분류 튜닝** (위험 키워드·일반어·이해관계자 가중) → `classify-tuning.yaml`
7. **PR 검색·톤 렉시콘·매체명** → `pr-queries.yaml`, `tone-lexicon.yaml`, `media.yaml`
   - 자사명 변형을 모두 묻는다: 한글·영문 공식명, **띄어쓰기/공백 유무 변형**, 로마자·약칭·
     구 사명·티커. 영문 변형은 **소문자판도 함께** `self_aliases` 에 넣는다(매칭 대소문자 이슈).
8. **발송 대상** (그룹별 수신자) → `delivery.yaml` 의 `recipients`(시크릿 아님)

생성 후 각 파일을 `python3 -c "import yaml; yaml.safe_load(open(...))"` 로 검증한다.

> [!IMPORTANT]
> **편집 품질의 한계(`prompt-examples.yaml`)**: 인사이트 합성의 few-shot 예시(좋은 인사이트/거짓 유추/날조 수치 반례)는 그 조직의 *실제 과거 브리핑*과 편집 판단이 있어야 만들 수 있어 **자동 생성되지 않는다**. 마법사는 빈 슬롯 스키마만 깔고, "format(형식)은 강제되지만 편집 품질은 직접 `config/prompt-examples.yaml` 큐레이션이 필요하다"고 안내한다. 1호(번들 예시 도메인팩)는 기존 예시 보유.

---

## SECRETS — Azure 인증 (키체인)

시크릿은 **YAML 에 쓰지 않는다.** 플러그인 설정(userConfig)으로 받아 OS 키체인에 저장된다.

```
사내 이메일 발송(Microsoft Graph, Mail.Send)을 위해 Azure AD 앱 3개 값이 필요합니다.
IT 관리자에게 요청해 받은 뒤, Claude Code 플러그인 설정에서 입력하세요:
  - azure_tenant_id
  - azure_client_id
  - azure_client_secret  (민감 — 키체인 저장)
  - email_from           (발신 주소)
```
입력 후:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/send-email.py" --validate
```
연결 정상이면 ✅. (값은 채팅에 표시하지 않는다.)

대체 경로(개발/비플러그인): `config/delivery.yaml` 의 `email.azure` 에 직접. 단 `.gitignore` 필수.

---

## RECIPIENTS / KEYWORDS

- 수신자: `config/delivery.yaml` 의 `recipients` 그룹 편집. "경영진에 cfo@ 추가" → 해당 그룹 `to:` 에 추가. "파일럿 끄기" → `pilot_mode: false`.
- 키워드: `config/keywords.yaml` 의 `boost`/`exclude` 편집.
변경 후 "내일 실행부터 반영됩니다" 안내.

---

## ROUTINES — 자동 실행 등록

루틴 정의는 `routines/*.md`. **스케줄 상태는 패키징으로 옮겨지지 않는다**(scheduled-tasks MCP/데스크탑의 외부 메타데이터). 따라서 설치 시 **등록**이 필요하다.

1. `routines/{pr-monitoring-daily,newsletter-insight-mwf}.md` 의 작업경로를 `${CLAUDE_PROJECT_DIR}` 로 치환해 사용.
2. scheduled-tasks MCP(`create_scheduled_task`)로 등록하거나, 데스크탑 앱 Routines 에서 추가.
3. 각 루틴을 "Run Now" 1회 실행 → 권한(HTTP fetch·이메일·파일 IO) 사전 부여.

> [!IMPORTANT]
> **로컬 데스크탑 전용.** 루틴은 Claude Code 데스크탑 앱이 켜져 있을 때만 발화한다. Cowork(클라우드)는 수집이 차단돼 동작하지 않는다.

---

## 키 관리 원칙

- 시크릿은 키체인(userConfig). YAML 평문 금지. 채팅에 키 값 표시 금지(✅/❌만).
- `config/delivery.yaml`·`.env` 는 `.gitignore` 대상. `keywords.yaml` 등 도메인팩은 추적 가능(시크릿 없음).
- 사용자가 키를 채팅에 입력하면 즉시 안내만 하고 평문 저장하지 않는다(키체인 경로로 유도).
