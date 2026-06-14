---
name: newsletter
description: 산업군 인사이트 뉴스레터 생성·발송. 뉴스 수집 → 인사이트 합성 → HTML 렌더 → 품질 게이트 → 발송. 수집창 인자로 일간/주간 모두 커버.
argument-hint: "[date] [hours] — 예: '2026-06-08 48', '168'(주간) 또는 빈칸(오늘, pipelines.yaml 정책)"
---

# 뉴스레터 생성

⚠️ **`prmonitor` CLI(`newsletter` 서브커맨드)로 실행한다. 브리핑 내용을 텍스트/마크다운으로 직접 출력하지 않는다.**
스케줄 실행은 Routines 담당 (`routines/`) — 이 커맨드는 수동 생성 기능만.

## 인자 파싱

`$ARGUMENTS`에서:
- 첫 번째 토큰: 날짜 (YYYY-MM-DD). 없으면 오늘.
- 두 번째 토큰: 수집 시간(hours). 없으면 pipelines.yaml 정책 (48, 월요일 72). 168 이면 주간.

예시: `/newsletter` → 오늘, 정책 시간창 / `/newsletter 2026-06-08 168` → 주간

각종 설정 파일 존재 검증. `config/delivery.yaml` 없으면 `--no-email` 플래그를 추가한다.

---

## 실행

다음을 순서대로 실행한다:

1. **전처리 → 합성 → 후처리 (일괄)**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" newsletter $DATE ${HOURS:+--hours $HOURS}
```

(`$DATE`=$ARGUMENTS 첫 토큰 또는 오늘, `$HOURS`=둘째 토큰. 168이면 주간.)
이메일은 Azure 인증(플러그인 설정 또는 delivery.yaml)이 있을 때만 발송 — 없으면 HTML만 생성되고 자동 skip.

`prmonitor newsletter`가 내부적으로 다음을 자동 처리:
- `pre`(전처리) → `claude -p`(인사이트 합성) → `post`(HTML 렌더 + 발송)
- 수집 시간창은 `config/pipelines.yaml` 기준 자동 결정 (월요일 72)
- venv 자동 부트스트랩(첫 실행) · 실패 시 해당 단계 에러 출력 후 중단

---

## 완료 보고

실행 성공 후 다음만 출력:

```
✅ 브리핑 생성 완료
📄 data/output/newsletter/newsletter-report-{DATE}.html
```

**내용을 텍스트로 재현하지 않는다. HTML 파일 경로만 보고한다.**

---

## 실패 처리

`prmonitor newsletter`가 실패하면 에러 메시지를 그대로 출력하고 중단한다.\\
`data/processed/newsletter-briefing-{DATE}.json`이 생성됐는데 `prmonitor post` 단계만 실패한 경우엔 `prmonitor post`만 재시도 가능하다. 로그는 `logs/executions/` 폴더를 확인한다.
