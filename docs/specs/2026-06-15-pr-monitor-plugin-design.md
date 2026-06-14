# PR Monitor → Claude Code Plugin + 범용 템플릿: 설계서

작성일: 2026-06-15
상태: 승인됨 (범위 A — Phase 0~4 전부)

## 1. 목표

팀 단위 뉴스 자동화 도구 `pr-monitor`를 **공식 Claude Code 플러그인**으로 재포장하고,
**도메인 무지의 엔진 + 조직별 도메인팩** 구조로 분리하여 **산업 무관 범용 템플릿**으로 배포 가능하게 한다.
배포본에 번들되는 예시 도메인팩은 **Contoso Motors(EV)** — 공개 가능한 일러스트레이션 팩이다.
(실제 운영 조직의 도메인팩은 repo 밖에 비공개로 둔다.)

타깃 환경: **Claude Code Desktop App (Windows + macOS)**. Cowork(클라우드)는 웹 수집이 차단되어 미지원.

비목표(v1):
- Cowork/클라우드 실행. (수집 분리 서버를 두지 않는다 — "서버 없음" 원칙 유지)
- 도메인이 구조적으로 다른 파이프라인(주가/특허 등). 엔진은 "뉴스 수집→분류→합성→발송" 형태만.

## 2. 배경 사실 (공식 문서 검증)

- 단일 플러그인에 commands·agents·skills·hooks·MCP를 모두 번들 가능. (plugins-reference.md)
- 플러그인은 `~/.claude/plugins/cache/{mkt}/{plugin}/{ver}/`에 **읽기전용**으로 설치, 업데이트 시 교체.
- **`${CLAUDE_PLUGIN_DATA}`** (`~/.claude/plugins/data/{plugin-id}/`): 업데이트·재설치에도 살아남는 쓰기 디렉터리. venv·캐시용.
- **`${CLAUDE_PLUGIN_ROOT}`**: 플러그인 번들 루트(읽기전용). **`${CLAUDE_PROJECT_DIR}`**: Claude를 실행한 워크스페이스. 셋 다 크로스플랫폼.
- 시크릿은 `plugin.json`의 `userConfig`(`sensitive: true`) → OS 키체인.
- 첫 설치 전용 훅은 없음 → `SessionStart` 훅 + `.initialized` 마커로 멱등 초기화.
- Windows 훅은 bash(`.sh`)면 Git Bash 필요 → **Python 훅 권장**.

## 3. 아키텍처

### 3-1. 3-루트 경로 모델 (키스톤)

현재 `scripts/lib/common.sh:18`의 `cd "$PROJECT_ROOT"` 하나가 ~40개 bare 상대경로를 떠받친다.
플러그인은 단일 루트를 분해하므로 세 루트로 명시한다:

| 루트 | env | 내용 | 성격 |
|------|-----|------|------|
| 로직 | `${CLAUDE_PLUGIN_ROOT}` | scripts, agents, skills, commands, requirements.txt, `config-templates/`(엔진 기본값) | 읽기전용 캐시 |
| 워크스페이스 | `${CLAUDE_PROJECT_DIR}` | **도메인팩** `config/`, `data/self-context/`, `data/output/`, `logs/` | 보이는·백업·이전 가능 |
| 데이터캐시 | `${CLAUDE_PLUGIN_DATA}` | `.venv`, `data/raw`, `data/processed`, `data/cache` | 숨김·버려도 재생성 |

**Dev 폴백(Phase 0 디리스크):** `CLAUDE_*` env가 없으면 셋 다 repo 루트로 폴백 → 현재 repo에서 동작 동일.
유일한 단일 모듈 `prmonitor/paths.py`가 이 해석을 소유하고, `common.sh`/`common.py`가 그것만 참조한다.

### 3-2. bash → 단일 Python CLI

5개 오케스트레이터(`run-pre/post/pr-monitor/pr-daily/newsletter.sh`) + `common.sh`를
`python -m prmonitor <pre|post|pr|newsletter|setup>` 단일 CLI로 접는다.
오케스트레이터는 이미 전부 Python으로 shell-out하는 얇은 런처라 가능.
이 한 번의 리팩터로 Windows 표면(`source`/`BASH_SOURCE`, `.venv/bin/python3`, `trap`, bash 배열,
`find -mtime -delete`, `sed`, `date +%u`, ANSI)이 전부 제거된다.

**크로스플랫폼 venv 부트스트랩:** `shutil.which("python","py","python3")` → `venv.create()` →
OS별 `Scripts/python.exe`(Win) vs `bin/python3`(POSIX) 분기. venv는 `${CLAUDE_PLUGIN_DATA}/.venv`.

### 3-3. 엔진 / 도메인팩 분리

오염 집중 파일(우선순위순): `gen-pr-monitor.py`(최악) > `format.py` > `classify.py` > `insight-synthesizer.md`.

도메인팩(= `/setup`이 `${CLAUDE_PROJECT_DIR}/config/`에 생성)으로 추출할 항목:

| 도메인팩 파일 | 현재 위치(하드코딩) | 내용 |
|---|---|---|
| `company-profile.yaml` | 이미 config (정본) | 회사·경쟁사·카테고리·공급망 |
| `categories.yaml` | `format.py` CAT_COLORS/CAT_NAMES/CAT_ORDER | 카테고리 id·라벨·색 |
| `style.yaml` | **3중 중복** (CLAUDE.md + insight-synthesizer.md + format.py) | 톤 블랙리스트·금지어미·문장길이·언어·뉴스레터 제목 |
| `classify-tuning.yaml` | `classify.py` 8개 블록 | RISK_TITLE_KW·generic_terms·stakeholder_boosts·low_signal·expo |
| `tone-lexicon.yaml` | `gen-pr-monitor.py` NEG/POS/STOCK_KW | PR 톤 판정 렉시콘 |
| `media.yaml` | `gen-pr-monitor.py` MEDIA_NAMES + DOMAIN_TO_NAME, `format.py` media-mapping | 매체명·도메인 매핑 (현재 두 곳 분기 — 통합) |
| `branding.yaml` | `gen-pr-monitor.py`·`format.py` HTML 문자열 | 조직명·부서·태그라인 |
| `pr-queries.yaml` | `gen-pr-monitor.py` SELF_KW/GNEWS_QUERIES | 자사 별칭·검색쿼리 |
| `prompt-examples.yaml` | `insight-synthesizer.md` few-shot | ⚠️ 자동생성 불가 (§5) |
| `triggers.yaml` | CLAUDE.md 한국어 트리거구 | 로케일별 |

엔진은 위를 **데이터로 읽어** 동작. 톤 블랙리스트는 `style.yaml` **단일 출처**로 통합.

### 3-4. 시크릿 → userConfig → 키체인

`config/delivery.yaml`의 `email.azure.{tenant_id, client_id, client_secret}` → `plugin.json` userConfig
(`client_secret` 최소 sensitive). `send-email.py`는 env(`AZURE_CLIENT_SECRET` 등)에서 읽는다.
비밀 아닌 부분(`recipients`, `pilot_mode`, `from`)은 보이는 `${CLAUDE_PROJECT_DIR}/config/recipients.yaml`.

### 3-5. `/setup` = 도메인팩 생성기 + 루틴 등록기

현재 `/setup`은 2개 파일(delivery/keywords) 편집기일 뿐. 범용을 위해 **인터뷰 마법사**로 격상:
회사명·산업·경쟁사·카테고리·키워드·소스·언어·발송대상을 대화로 받아 §3-3 도메인팩 전체를 생성.
시크릿은 userConfig로, 루틴은 scheduled-tasks MCP로 등록(§5-2). 이것이 "플러그인용 플러그인"의 실체 —
코드가 아니라 **데이터(config)를 생성**하므로 엔진은 공유되고 업데이트가 전파된다.

### 3-6. 첫 실행 스캐폴딩

`hooks/hooks.json`의 `SessionStart`(startup) → `python "${CLAUDE_PLUGIN_ROOT}/prmonitor/init.py"`(bash 아님 — Windows):
`${CLAUDE_PROJECT_DIR}/.prmonitor-initialized` 마커 확인 → 없으면 config/data 골격 스캐폴딩 +
`${CLAUDE_PLUGIN_DATA}/.venv` 부트스트랩. 멱등.

## 4. 디렉터리 레이아웃 (플러그인)

```
PluginProject/                         ← 플러그인 repo (= ${CLAUDE_PLUGIN_ROOT} when installed)
├── .claude-plugin/
│   ├── plugin.json                    # 매니페스트 + userConfig(secrets)
│   └── marketplace.json               # 셀프호스트 마켓플레이스
├── commands/        newsletter.md, pr-clipping.md, setup.md   (.claude/ 접두 제거)
├── agents/          insight-synthesizer.md, self-context-updater.md
├── skills/          article-extractor/, briefing-formatter/
├── hooks/           hooks.json (SessionStart init)
├── scripts/         pipeline·pr·newsletter·lib (Python; bash 폐기)
├── prmonitor/       paths.py, __main__.py(CLI), common.py, init.py, bootstrap.py
├── config-templates/  엔진 기본값 + Contoso Motors(EV) 예시 도메인팩(배포본 샘플)
├── requirements.txt
└── docs/specs/

${CLAUDE_PROJECT_DIR}/  (사용자 워크스페이스, /setup·init이 생성)
├── config/         company-profile·categories·style·classify-tuning·...·recipients.yaml
├── data/self-context/, data/output/
└── logs/

${CLAUDE_PLUGIN_DATA}/  (숨김)
└── .venv/, data/raw, data/processed, data/cache
```

## 5. 정직한 한계 (설계가 못 없애는 것)

1. **few-shot 추론 예시는 자동 생성 불가.** `insight-synthesizer.md`의 판단-전달 예시(삼성 104억 7.5배 등)는
   토큰 치환이 아니라 *편집 판단*을 가르치는 자산. `/setup`은 키워드·경쟁사는 인터뷰로 뽑아도 이건 못 만든다.
   → 새 조직은 format은 강제되나 편집 품질은 직접 `prompt-examples.yaml` 큐레이션. **"범용"의 천장.**
   (예시 팩 Contoso Motors는 일러스트레이션용 예시를 동봉 → 형식 참고용.)
2. **스케줄링은 패키징으로 안 옮겨진다.** cron 상태는 scheduled-tasks MCP/데스크탑의 외부 메타데이터.
   플러그인 업데이트는 프롬프트만 갱신. → `/setup`이 MCP로 (재)등록. 데스크탑 로컬 실행 전용 제약 노출.

## 6. 단계별 마이그레이션 (각 단계 독립 출하, 회사 도구 내내 작동)

- **Phase 0 — 경로 추상화:** `prmonitor/paths.py` 3-루트 해석 + dev 폴백. `common.py`/`common.sh`가 참조. 아직 이동 없음.
- **Phase 1 — bash→Python CLI:** `common.sh`→`common.py`, 5 오케스트레이터→`python -m prmonitor` 서브커맨드, 크로스플랫폼 venv. **Windows 잠금 해제.**
- **Phase 2 — 플러그인 패키징:** `plugin.json`·`marketplace.json`·`.claude/` 접두 제거·`hooks.json`(SessionStart)·userConfig 시크릿. **배포 가능.**
- **Phase 3 — 엔진/도메인 추출:** 하드코딩 → 도메인팩 config(§3-3). 톤 블랙리스트 단일화. 배포본 예시 = Contoso Motors(EV) 도메인팩.
- **Phase 4 — `/setup` 생성기:** 인터뷰 → 도메인팩 생성 + 루틴 등록. **범용 완성.**

## 7. 검증

- 기존 pytest 98개(classify·aggregate·format·quality_gates)는 회귀 가드. 포팅 후 전부 통과 유지.
- Phase 1: macOS에서 `python -m prmonitor pre` 가 기존 `run-pre.sh`와 동일 산출물 생성(골든 비교).
- Windows: 최소 venv 부트스트랩 + `pre` 1스텝이 Git Bash 없이 동작(수동/CI).
- Phase 3: 도메인팩을 비우면 엔진이 "도메인 미설정" 에러로 안전 정지(하드코딩 잔재 없음 증명).
```
