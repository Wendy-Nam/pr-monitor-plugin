# Routines 정의 (정본)

**이 폴더가 정본(source of truth)이다.** 루틴 프롬프트를 수정하면 여기서 고치고 커밋한다.

이 `routines/*.md` 가 곧 루틴 프롬프트의 정본이다. 플러그인은 이 파일을
scheduled-tasks MCP 로 **등록**해 실행본을 만든다 — 등록 메타데이터(cron·enabled)는
패키징으로 옮겨지지 않는 외부 상태라, 설치 때마다 한 번 등록해 줘야 한다.

| 파일 | task-id | 스케줄 |
|------|---------|--------|
| pr-monitoring-daily.md | pr-monitoring-daily | 평일 10:30 (+jitter) |
| newsletter-insight-mwf.md | newsletter-insight-mwf | 월·수·금 09:30 (+jitter) |

작업 디렉토리: `${CLAUDE_PROJECT_DIR}` (실 config·시크릿이 있는 워크스페이스).
루틴 프롬프트 안의 작업경로 자리표시자는 등록 시 이 값으로 치환된다.

## 루틴 등록 (정본 → 실행본)

루틴은 동기화 스크립트가 아니라 **플러그인 등록 플로우**로 실행본이 된다.
새 설치이거나, 루틴 프롬프트를 고쳐 다시 반영해야 할 때:

```
/setup 루틴 등록      # → ROUTINES 플로우 실행
```

`/setup ROUTINES` 는 다음을 한다:

1. `routines/{pr-monitoring-daily,newsletter-insight-mwf}.md` 의 작업경로
   자리표시자를 `${CLAUDE_PROJECT_DIR}` 로 치환한다.
2. scheduled-tasks MCP(`create_scheduled_task`)로 각 루틴을 등록한다 —
   cron 스케줄·enabled 상태는 이 MCP 가 외부 메타데이터로 관리한다.
   (또는 데스크톱 앱 Routines 화면에서 직접 추가해도 된다.)
3. 각 루틴을 "Run Now" 로 1회 실행해 권한(HTTP fetch·이메일·파일 IO)을
   사전 부여한다.

프롬프트만 고친 경우에도 같은 등록 플로우를 다시 돌리면 실행본이 갱신된다.
별도의 파일 복사 단계는 없다.

## 실행 주체: Claude Code Routines (데스크톱 앱)

**Routines 는 Claude Code 데스크톱 앱이 켜져 있어야 실행된다.** 앱이 꺼져 있으면 다음 실행 때 보충된다.
Routines 컨텍스트는 로컬 셸 전권을 가진다 — 외부 HTTP 수집, Azure 이메일 발송, 파일 I/O 모두 정상 동작.

**Cowork(클라우드)에서는 동작하지 않는다** — 샌드박스가 외부 fetch 를 차단해 수집이 0건으로 실패한다.

파이프라인 실행 흐름:
```
Claude Code Routines (데스크톱 앱)
  → bash scripts (scripts/newsletter/ or scripts/pr/)
    → Python (외부 HTTP 수집, LLM 호출, 이메일 발송)
      → 산출물 (data/output/{newsletter,pr}/*.html)
```

## 첫 등록 후

각 루틴을 **Run Now 로 1회 수동 실행**해 권한을 미리 허용해둔다.
이후 예약 실행은 저장된 권한을 자동 적용해 사용자 확인 없이 끝까지 돈다.
