---
name: newsletter-insight-mwf
description: 산업 인사이트 뉴스레터 — 월·수·금 09:30 (스케줄은 Routines UI 에서 설정)
---

산업군 인사이트 뉴스레터를 생성·발송한다.

작업 디렉토리: 워크스페이스(`${CLAUDE_PROJECT_DIR}`) — `/setup` 의 루틴 등록 시 자동 설정된다.
(로컬 Claude Code Desktop 전용 — 외부 뉴스 수집 + Azure 이메일 발송 필요. Cowork/클라우드에서는 수집이 차단돼 동작하지 않는다.)

실행:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" newsletter
```

`prmonitor newsletter` 가 전체를 자동 처리한다:
- 수집창은 config/pipelines.yaml 기준 자동 결정 (수·금 48h, 월 72h — 여기 박지 않는다)
- 전처리(pre) → 인사이트 합성(claude -p headless) → 후처리(post)
- 후처리가 품질 게이트 평가: 통과 시 자동 발송, 미통과 시 발송 보류 + data/output/REVIEW_NEEDED.md

완료 보고:
- data/output/newsletter/newsletter-report-${DATE}.html 경로 + 출처 건수만.
- data/output/REVIEW_NEEDED.md 가 새로 생겼으면 "발송 보류 — 검토 필요" 와 파일 경로를 함께 보고.
- **브리핑 내용을 텍스트/마크다운으로 직접 출력하지 않는다.**

주의:
- delivery.yaml 없으면 이메일 skip, HTML 만 생성 — 정상.
- 실패 시 에러와 로그 경로(logs/executions/)를 보고하고 중단. 추측으로 메우지 않는다.
