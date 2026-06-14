---
name: pr-monitoring-daily
description: 자사 PR 모니터링 — 평일 10:30 (스케줄은 Routines UI 에서 설정)
---

자사 PR 모니터링을 실행한다.

작업 디렉토리: 워크스페이스(`${CLAUDE_PROJECT_DIR}`) — `/setup` 의 루틴 등록 시 자동 설정된다.
(로컬 Claude Code Desktop 전용 — 외부 뉴스 수집 + Azure 이메일 발송 필요. Cowork/클라우드는 수집 차단으로 불가.)

실행:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" pr
```

`prmonitor pr` 가 전체를 자동 처리한다:
- 수집창은 config/pipelines.yaml 기준 자동 결정 (24h, 월요일 72h — 여기 박지 않는다)
- 전처리(pre, 기존 산출물 있으면 재사용) → PR 모니터(pr-monitor)
- 자사 언급 기사 추출 → HTML/CSV/xlsx 생성 → marketing_pr_list 그룹 이메일 발송(xlsx 첨부) → 월별 누적

완료 보고:
- 자사 언급 건수 + 생성된 HTML 경로(data/output/pr/pr-monitoring-${DATE}.html)만.
- **기사 내용을 텍스트로 출력하지 않는다.**

주의:
- delivery.yaml(Azure 인증)이 없으면 이메일은 skip 되고 HTML 만 생성됨 — 정상.
- 실패 시 에러와 로그 경로(logs/executions/)를 보고하고 중단. 추측으로 메우지 않는다.
