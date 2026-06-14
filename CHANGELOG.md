# Changelog

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
