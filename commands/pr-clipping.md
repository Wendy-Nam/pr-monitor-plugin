---
name: pr-clipping
description: 자사 PR 클리핑 생성·발송. 자사 언급 기사 수집 → 톤 판정 → HTML/CSV/XLSX → 발송 + 월별 누적.
argument-hint: "[date] [hours] — 예: '2026-06-08 72' / '2026-06-08' / 빈칸(오늘·정책 시간창)"
---

# PR 클리핑 생성

자사 언급 기사를 수집해 PR 클리핑 리포트를 만든다.
스케줄 실행은 Routines 담당 (`routines/`) — 이 커맨드는 수동 생성 기능만.

## 인자 파싱

`$ARGUMENTS` 에서:
- 첫 번째 토큰: 날짜 (YYYY-MM-DD). 없으면 오늘.
- 두 번째 토큰: 수집 시간(hours). 없으면 pipelines.yaml 정책(24h, 월요일 72h 주말 포함).
  매일 안 돌리는 경우 이 값으로 수집창을 넓힌다 (예: 주 1회면 `168`).

## 실행

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" pr $DATE ${HOURS:+$HOURS}
```

- DATE 인자(첫 토큰)가 있으면 그대로, 없으면 오늘.
- HOURS 인자(둘째 토큰)가 있으면 그 수집창, 없으면 pipelines.yaml 자동 해석 (24h, 월요일 72h).
- 전처리 산출물이 이미 있으면 `pre` 단계가 재사용 (월·수·금은 뉴스레터 Routine 이 미리 만들어둠).
- Azure 인증(플러그인 설정 또는 delivery.yaml) 없으면 이메일 skip, HTML 만 생성 — 정상.

## 완료 보고

```
✅ PR 모니터링 완료 (자사 언급 N건)
📋 data/output/pr/pr-monitoring-{DATE}.html
```

**기사 내용을 텍스트로 출력하지 않는다. 건수와 파일 경로만 보고한다.**

## 실패 처리

에러 메시지와 로그 경로(`logs/executions/`)를 보고하고 중단한다. 추측으로 메우지 않는다.
