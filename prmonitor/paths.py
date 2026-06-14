"""Three-root path resolution — the keystone of the plugin refactor.

The original code assumed ONE flat root (``cd $PROJECT_ROOT`` + relative paths).
The plugin model demolishes that: logic is read-only, config/data are writable
and live elsewhere. This module is the single owner of where everything lives.

Three roots (all overridable by Claude Code env vars, cross-platform):

  PLUGIN_ROOT  ${CLAUDE_PLUGIN_ROOT}   read-only logic   scripts, agents, skills, requirements.txt, config-templates
  PROJECT_DIR  ${CLAUDE_PROJECT_DIR}   visible workspace  domain-pack config, self-context, output, logs
  PLUGIN_DATA  ${CLAUDE_PLUGIN_DATA}   hidden cache       .venv, raw, processed, cache  (survives plugin updates)

Dev fallback (Phase 0 de-risk): when none of the CLAUDE_* env vars are set
(i.e. running straight from the repo, not as an installed plugin), all three
collapse to the repo root — reproducing the ORIGINAL flat layout exactly, so
existing behaviour and tests are unchanged.

Run ``python -m prmonitor.paths`` to print shell-eval'able exports, so a
transitional ``common.sh`` can adopt these paths without duplicating the logic.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name)
    return Path(val).expanduser().resolve() if val else None


# prmonitor/paths.py -> parent.parent == repo root (dev) or plugin bundle root (installed).
_BUNDLE_ROOT = Path(__file__).resolve().parent.parent

# ── the three roots ───────────────────────────────────────────────────────────
PLUGIN_ROOT: Path = _env_path("CLAUDE_PLUGIN_ROOT") or _BUNDLE_ROOT
PROJECT_DIR: Path = _env_path("CLAUDE_PROJECT_DIR") or _BUNDLE_ROOT
PLUGIN_DATA: Path = _env_path("CLAUDE_PLUGIN_DATA") or _BUNDLE_ROOT

#: True when running as an installed plugin (roots genuinely diverge).
IS_PLUGIN: bool = _env_path("CLAUDE_PLUGIN_ROOT") is not None

# ── logic (read-only, under PLUGIN_ROOT) ──────────────────────────────────────
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REQUIREMENTS = PLUGIN_ROOT / "requirements.txt"
CONFIG_TEMPLATES = PLUGIN_ROOT / "config-templates"
AGENTS_DIR = PLUGIN_ROOT / "agents"
SKILLS_DIR = PLUGIN_ROOT / "skills"

# ── domain pack + user-visible assets (under PROJECT_DIR) ──────────────────────
CONFIG_DIR = PROJECT_DIR / "config"
SELF_CONTEXT_DIR = PROJECT_DIR / "data" / "self-context"
OUTPUT_DIR = PROJECT_DIR / "data" / "output"
NEWSLETTER_OUTPUT_DIR = OUTPUT_DIR / "newsletter"
PR_OUTPUT_DIR = OUTPUT_DIR / "pr"
LOGS_DIR = PROJECT_DIR / "logs" / "executions"
INIT_MARKER = PROJECT_DIR / ".prmonitor-initialized"

# ── disposable caches + venv (under PLUGIN_DATA, hidden, survives updates) ─────
RAW_DIR = PLUGIN_DATA / "data" / "raw"
PROCESSED_DIR = PLUGIN_DATA / "data" / "processed"
CACHE_DIR = PLUGIN_DATA / "data" / "cache"
VENV_DIR = PLUGIN_DATA / ".venv"


def venv_python() -> Path:
    """Cross-platform path to the venv interpreter (Windows: Scripts\\python.exe)."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python3"


def venv_pip() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"


def ensure_dirs() -> None:
    """Create the writable dir skeleton (idempotent). Never touches PLUGIN_ROOT."""
    for d in (
        CONFIG_DIR, SELF_CONTEXT_DIR, NEWSLETTER_OUTPUT_DIR, PR_OUTPUT_DIR,
        LOGS_DIR, RAW_DIR, PROCESSED_DIR, CACHE_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def as_exports() -> str:
    """Shell-eval'able exports for a transitional common.sh (`eval "$(python -m prmonitor.paths)"`)."""
    pairs = {
        "PRM_PLUGIN_ROOT": PLUGIN_ROOT, "PRM_PROJECT_DIR": PROJECT_DIR,
        "PRM_PLUGIN_DATA": PLUGIN_DATA, "PRM_CONFIG_DIR": CONFIG_DIR,
        "PRM_RAW_DIR": RAW_DIR, "PRM_PROCESSED_DIR": PROCESSED_DIR,
        "PRM_CACHE_DIR": CACHE_DIR, "PRM_OUTPUT_DIR": OUTPUT_DIR,
        "PRM_NEWSLETTER_OUTPUT_DIR": NEWSLETTER_OUTPUT_DIR, "PRM_PR_OUTPUT_DIR": PR_OUTPUT_DIR,
        "PRM_SELF_CONTEXT_DIR": SELF_CONTEXT_DIR, "PRM_LOGS_DIR": LOGS_DIR,
        "PRM_SCRIPTS_DIR": SCRIPTS_DIR, "PRM_VENV_DIR": VENV_DIR,
        "PRM_VENV_PY": venv_python(), "PRM_REQUIREMENTS": REQUIREMENTS,
    }
    return "\n".join(f'export {k}="{v}"' for k, v in pairs.items())


if __name__ == "__main__":
    print(as_exports())
