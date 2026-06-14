"""Domain-pack loader — the engine reads org-specific knowledge from here.

Resolution order for ``<name>.yaml``:
  1. ${CLAUDE_PROJECT_DIR}/config/<name>.yaml   (user's domain pack, written by /setup)
  2. ${CLAUDE_PLUGIN_ROOT}/config-templates/<name>.yaml  (bundled domain pack #1)
  3. raise DomainPackError

So the engine carries NO hardcoded org knowledge: an org runs /setup to write (1);
the bundled example pack lives in (2) as the default/example; a truly empty install
fails loudly instead of silently using stale baked-in data.
"""
from __future__ import annotations

from pathlib import Path

from . import paths


class DomainPackError(RuntimeError):
    pass


def pack_path(name: str) -> Path | None:
    for base in (paths.CONFIG_DIR, paths.CONFIG_TEMPLATES):
        p = base / f"{name}.yaml"
        if p.is_file():
            return p
    return None


def load_pack(name: str) -> dict:
    """Load a domain-pack YAML by stem (e.g. 'style', 'classify-tuning')."""
    import yaml
    p = pack_path(name)
    if p is None:
        raise DomainPackError(
            f"도메인팩 '{name}.yaml' 을 찾지 못했습니다. /setup 으로 생성하거나 "
            f"config-templates/{name}.yaml 을 확인하세요."
        )
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get(name: str, key: str, default=None):
    """Convenience: a single top-level key from a pack, with a default."""
    try:
        return load_pack(name).get(key, default)
    except DomainPackError:
        return default
