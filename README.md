# PR Monitor — Claude Code Plugin

뉴스 자동화 플러그인. 두 산출물을 만든다:

| 산출물 | 빈도 | 내용 |
|--------|------|------|
| **산업 인사이트 뉴스레터** | 월·수·금 | 인사이트 3개 + 카테고리 동향 + 출처 |
| **PR 모니터링** | 평일 매일 | 자사 언급 기사 + 톤 라벨 + 월간 엑셀 |

수집·분류·렌더링은 **결정론 코드**, LLM 은 인사이트 합성·PR 톤 판정 두 곳에만. 1회 실행 ~$0.25.

> [!IMPORTANT]
> **실행 환경**: 로컬 **Claude Code Desktop 앱** (Windows · macOS). 뉴스 사이트를 직접 수집하므로
> 네트워크가 필요하다. **Cowork(클라우드)에서는 수집이 차단돼 동작하지 않는다.**

## 설계: 엔진 + 도메인팩

엔진은 **회사·산업을 모른다.** 조직별 지식(회사·경쟁사·카테고리·키워드·소스·톤)은 전부
`config/` 의 **도메인팩 YAML** 에 있고, 엔진은 그걸 읽어 동작한다. 새 조직은 `/setup` 인터뷰로
자기 도메인팩을 만든다 — **같은 엔진, 각자의 도메인팩.** (고객마다 코드를 fork 하지 않는다.)

3-루트 경로 모델로 깨끗이 분리된다:
- **로직** → 플러그인(`${CLAUDE_PLUGIN_ROOT}`, 읽기전용, 업데이트 대상)
- **도메인팩·산출물** → 워크스페이스(`${CLAUDE_PROJECT_DIR}`, 보이고 백업·이전 가능)
- **시크릿** → 플러그인 설정(`userConfig`) → OS 키체인 (평문 YAML 금지)
- **venv·캐시** → `${CLAUDE_PLUGIN_DATA}` (숨김, 업데이트에도 보존)

## 설치

```
/plugin marketplace add Wendy-Nam/pr-monitor
/plugin install pr-monitor@news-monitor
```

설치 시 Azure 이메일 인증(`azure_tenant_id`/`azure_client_id`/`azure_client_secret`/`email_from`)을
물어본다 — 입력값은 키체인에 저장된다(생략 가능, 이메일 발송만 비활성).

첫 세션에서 `SessionStart` 훅이 워크스페이스에 `config/`·`data/` 골격을 깔고 Python venv 를 자동 구축한다.

## 첫 설정

```
/setup
```
- **배포본 예시 도메인팩(Contoso Motors · EV)으로 시작** 하거나
- **새 조직 자동 초안(권장)** — "회사명 + 산업"만 주면 `setup-bootstrap` 에이전트가 웹 리서치로 경쟁사·카테고리·키워드·소스를 조사해 도메인팩 초안을 만들고, 요약을 제시해 승인받는다(완전 자동 아님). 조직당 1회.
- **새 조직 수동 인터뷰** — 항목을 직접 입력해 도메인팩을 생성.

> few-shot 인사이트 예시(`prompt-examples.yaml`)는 조직의 실제 과거 브리핑이 있어야 해서 자동 생성되지 않는다 — 형식 플레이스홀더만 깔리고, 편집 품질은 운영하며 직접 큐레이션한다.

이메일·수신자·키워드·루틴 등록도 `/setup` 에서. (자세히는 커맨드 안내 참조.)

## 사용

| 명령 | 동작 |
|------|------|
| `/newsletter [date] [hours]` | 인사이트 뉴스레터 생성·발송 (168=주간) |
| `/pr-clipping [date]` | 자사 PR 클리핑 생성·발송 |
| `/setup` | 설정·상태·시크릿·수신자·키워드·루틴 |

자연어로도 동작한다("오늘 브리핑", "PR 모니터링", "상태 보여줘").

내부적으로는 크로스플랫폼 CLI 가 돈다:
```
python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" <pre|post|pr|newsletter|init|paths>
```

## 자동 실행 (Routines)

`routines/` 의 두 루틴(PR 평일 10:30, 뉴스레터 월·수·금 09:30)을 `/setup` 의 ROUTINES 에서
scheduled-tasks 로 등록한다. **스케줄 상태는 패키징으로 옮겨지지 않으므로 설치 시 등록이 필요하다.**
루틴은 데스크탑 앱이 켜져 있을 때만 발화한다.

## 한계 (정직하게)

- **편집 품질의 천장**: 인사이트 합성의 few-shot 판단 예시(`config/prompt-examples.yaml`)는
  자동 생성되지 않는다. 새 조직은 형식은 강제되나 편집 품질은 운영하며 직접 큐레이션해야 한다.
- **Cowork 미지원**: 클라우드 샌드박스는 외부 수집을 막는다. 로컬 데스크탑 전용.

## 개발

```bash
.venv/bin/python3 -m pytest tests/ -q     # 98개 회귀 테스트
python3 -m prmonitor paths                 # 해석된 3-루트 확인
```
참조 원본은 `ref-pr-monitor/`(분석용 클론, 패키징 제외). 설계 상세: [docs/specs/](docs/specs/).
