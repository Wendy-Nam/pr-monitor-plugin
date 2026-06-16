"""번들 도메인팩 스모크 테스트 — `/setup` 의 "예시 팩으로 시작" 케이스 안전망.

엔진은 도메인팩 YAML 을 읽어 동작하고, 일부는 **모듈 import 시점**에 팩을 읽는다
(classify._TUNING, aggregate._classify_tuning). 그래서 번들 팩의 키 누락·타입 오류·
빈 리스트 등이 곧장 import 크래시로 이어진다 — 실제로 빈 `stakeholder_boosts` 가
`[0]` 접근에서 IndexError 를 냈던 부류다. 이 테스트가 그걸 출고 전에 잡는다.

B·C 는 전역 모듈 상태(_TUNING 등) 오염을 피하려 **서브프로세스로 격리** 실행한다.

세 가지:
  A. 번들 config-templates 의 모든 팩이 파싱되고 엔진이 의존하는 스키마를 만족
  B. 빈 워크스페이스(=갓 설치, 예시 팩만) 에서 엔진 모듈이 크래시 없이 import
  C. init 이 config 를 시드하고 멱등이며 시크릿(delivery.yaml)은 시드하지 않음
"""
import os
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config-templates"


def _load(name: str):
    return yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))


def _run_py(code: str, workspace: Path, data: Path):
    """번들 팩만 있는 빈 워크스페이스 env 로 파이썬 코드를 격리 실행."""
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(workspace),     # config/ 없음 → config-templates 폴백
        "CLAUDE_PLUGIN_ROOT": str(PROJECT_ROOT),
        "CLAUDE_PLUGIN_DATA": str(data),
    }
    pre = f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); sys.path.insert(0, {str(PROJECT_ROOT / 'scripts')!r})\n"
    return subprocess.run([sys.executable, "-c", pre + code], env=env,
                          capture_output=True, text=True)


# ── A. 번들 팩 파싱 + 스키마 불변식 ───────────────────────────
class TestBundledPacksSchema:
    def test_all_packs_parse(self):
        packs = list(TEMPLATES.glob("*.yaml"))
        assert packs, "config-templates 에 팩이 없음"
        for f in packs:
            assert yaml.safe_load(f.read_text(encoding="utf-8")) is not None, f"{f.name} 비었음/파싱 실패"

    def test_classify_tuning_keys_are_lists(self):
        """classify/aggregate 가 [..] 로 직접 인덱싱/순회하는 키 — 반드시 list."""
        t = _load("classify-tuning.yaml")
        for key in ("risk_title_keywords", "generic_category_terms", "listing_markers",
                    "expo_patterns", "low_signal_title_patterns", "low_signal_body_patterns",
                    "stakeholder_boosts"):
            assert isinstance(t.get(key), list), f"classify-tuning.{key} 가 list 아님"

    def test_keywords_keys_are_lists(self):
        kw = _load("keywords.yaml")
        for key in ("boost", "exclude", "strong_exclude", "exclude_domains"):
            assert isinstance(kw.get(key, []), list), f"keywords.{key} 가 list 아님"

    def test_categories_and_profile_shape(self):
        cats = _load("categories.yaml")
        assert isinstance(cats.get("categories"), dict) and cats["categories"]
        prof = _load("company-profile.yaml")
        assert isinstance(prof.get("company"), dict)
        for cid, ci in (prof.get("categories") or {}).items():
            assert isinstance(ci.get("watch_keywords", []), list), f"{cid}.watch_keywords"
            assert isinstance(ci.get("key_players", []), list), f"{cid}.key_players"

    def test_style_has_language_and_sentence_max(self):
        s = _load("style.yaml")
        assert isinstance(s.get("language"), str) and s["language"]
        assert isinstance(s.get("sentence_max"), int)


# ── B. 빈 워크스페이스(예시 팩만)에서 엔진 import 무크래시 ──────
class TestEngineConsumesBundledPacks:
    def test_engine_modules_import_on_bundled_pack(self, tmp_path):
        """import 시 load_pack('classify-tuning') 실행 — 빈 리스트 등으로 크래시하면
        서브프로세스가 non-zero 로 죽는다 (과거 stakeholder_boosts[0] IndexError 회귀)."""
        r = _run_py(
            "import scripts.pipeline.classify, scripts.pipeline.aggregate; print('ok')",
            tmp_path / "ws", tmp_path / "data")
        assert r.returncode == 0, f"번들 팩으로 엔진 import 실패:\n{r.stderr}"
        assert "ok" in r.stdout


# ── C. init 시드 + 멱등 + 시크릿 미시드 ───────────────────────
class TestInitScaffolding:
    def test_seeds_config_idempotent_no_secret(self, tmp_path):
        ws, data = tmp_path / "ws", tmp_path / "data"
        r = _run_py(
            "from prmonitor.steps import init; "
            "import sys; sys.exit(init.run() or init.run())",  # 두 번 = 멱등 확인
            ws, data)
        assert r.returncode == 0, f"init 실패:\n{r.stderr}"
        cfg = ws / "config"
        assert (cfg / "company-profile.yaml").is_file(), "config 시드 안 됨"
        # 시크릿 파일은 절대 시드하지 않는다 (userConfig→키체인 경로)
        assert not (cfg / "delivery.yaml").is_file(), "delivery.yaml(시크릿) 시드됨"
