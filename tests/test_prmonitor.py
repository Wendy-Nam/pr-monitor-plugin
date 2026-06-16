"""Unit tests for the new prmonitor engine layer (paths / domainpack / common).

These cover the plugin refactor's keystone code, which had no coverage. They
import only the prmonitor package (not the pipeline step-scripts), so they run
independently of the rest of the suite.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_paths(monkeypatch, **env):
    """Reload prmonitor.paths under a controlled environment."""
    for k in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import prmonitor.paths as p
    return importlib.reload(p)


@pytest.fixture(autouse=True)
def _restore_paths_after_test():
    """Snapshot CLAUDE_* env, restore it, and reload paths after each test so the
    env-manipulating TestPaths cases don't leak a stale paths module into others."""
    keys = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA")
    snap = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import prmonitor.paths
    importlib.reload(prmonitor.paths)


class TestPaths:
    def test_dev_fallback_collapses_to_one_root(self, monkeypatch):
        p = _reload_paths(monkeypatch)  # no CLAUDE_* env
        assert p.IS_PLUGIN is False
        assert p.PLUGIN_ROOT == p.PROJECT_DIR == p.PLUGIN_DATA
        # derived dirs hang off the single root (original flat layout)
        assert p.CONFIG_DIR == p.PROJECT_DIR / "config"
        assert p.RAW_DIR == p.PLUGIN_DATA / "data" / "raw"

    def test_plugin_env_diverges(self, monkeypatch, tmp_path):
        root, proj, data = tmp_path / "r", tmp_path / "p", tmp_path / "d"
        p = _reload_paths(monkeypatch,
                          CLAUDE_PLUGIN_ROOT=str(root),
                          CLAUDE_PROJECT_DIR=str(proj),
                          CLAUDE_PLUGIN_DATA=str(data))
        assert p.IS_PLUGIN is True
        assert p.SCRIPTS_DIR == root.resolve() / "scripts"      # logic -> PLUGIN_ROOT
        assert p.CONFIG_DIR == proj.resolve() / "config"        # config -> PROJECT_DIR
        assert p.RAW_DIR == data.resolve() / "data" / "raw"     # cache -> PLUGIN_DATA
        assert p.VENV_DIR == data.resolve() / ".venv"

    def test_venv_python_is_os_specific(self, monkeypatch):
        p = _reload_paths(monkeypatch)
        leaf = p.venv_python().name
        assert leaf in ("python3", "python.exe")
        assert leaf == ("python.exe" if os.name == "nt" else "python3")

class TestDomainPack:
    def test_loads_bundled_pack(self):
        from prmonitor import domainpack as dp
        style = dp.load_pack("style")
        assert isinstance(style["sentence_max"], int) and style["sentence_max"] > 0
        assert isinstance(style["tone_blacklist"], list) and style["tone_blacklist"]

    def test_missing_pack_raises(self):
        from prmonitor import domainpack as dp
        with pytest.raises(dp.DomainPackError):
            dp.load_pack("definitely-not-a-pack")

    def test_get_with_default(self):
        from prmonitor import domainpack as dp
        assert dp.get("definitely-not-a-pack", "k", "fallback") == "fallback"


class TestCommon:
    def test_resolve_hours_monday_vs_other(self):
        from datetime import datetime
        from prmonitor import common as c
        mon, tue = datetime(2026, 6, 15), datetime(2026, 6, 16)
        # default pipeline cfg may be absent -> falls back to 24 for both; assert no crash + int
        assert isinstance(c.resolve_hours("newsletter", today=mon), int)
        assert isinstance(c.resolve_hours("newsletter", today=tue), int)

    def test_count_urls_shapes(self, tmp_path):
        from prmonitor import common as c
        flat = tmp_path / "flat.json"
        flat.write_text('{"urls": [1,2,3]}', encoding="utf-8")
        clustered = tmp_path / "cl.json"
        clustered.write_text('{"clusters": [{"article_count": 2}, {"article_count": 3}]}', encoding="utf-8")
        assert c.count_urls(flat) == 3
        assert c.count_urls(clustered) == 5
        assert c.count_urls(tmp_path / "missing.json") == 0

    def test_is_blog(self):
        from prmonitor import common as c
        assert c.is_blog("https://x.tistory.com/1") is True
        assert c.is_blog("https://therobotreport.com/a") is False


class TestCliArgs:
    """CLI 인자 — pr/pr-monitor 가 선택적 [hours] override 를 받는다."""

    def test_pr_accepts_optional_hours(self):
        from prmonitor.__main__ import build_parser
        p = build_parser()
        a = p.parse_args(["pr", "2026-06-15", "72"])
        assert a.date == "2026-06-15" and a.hours == 72
        # 생략 시 None → run() 이 정책값(월요일 72h 등)으로 폴백
        assert p.parse_args(["pr", "2026-06-15"]).hours is None
        assert p.parse_args(["pr"]).hours is None

    def test_pr_monitor_accepts_optional_hours(self):
        from prmonitor.__main__ import build_parser
        assert build_parser().parse_args(["pr-monitor", "2026-06-15", "48"]).hours == 48
