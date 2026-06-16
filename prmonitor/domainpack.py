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
from . import PrMonitorError


class DomainPackError(PrMonitorError):
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


# 예시 도메인팩에서 흘러나오는 자리표시 브랜딩(콘토소). 이게 보이면 회사명으로 대체.
_PLACEHOLDER_TOKENS = ("contoso", "콘토소")


def _ascii_name(*candidates) -> str:
    """후보들 중 ASCII(영문) 표기를 찾아 반환. 없으면 ''."""
    for c in candidates:
        for item in (c if isinstance(c, (list, tuple)) else [c]):
            s = (item or "").strip()
            if s and all(ord(ch) < 128 for ch in s):
                return s
    return ""


def company_names() -> tuple[str, str]:
    """(한글 회사명, 영문 회사명 or '') — company-profile / pr-queries 에서 도출."""
    try:
        prof = (load_pack("company-profile").get("company") or {})
    except DomainPackError:
        prof = {}
    ko = (prof.get("name") or "").strip()
    en = _ascii_name(prof.get("name_en"), prof.get("english_name"), prof.get("aliases"))
    if not en:
        en = _ascii_name(get("pr-queries", "self_aliases", []))
    return ko, en


def branding(key: str) -> str:
    """branding.yaml 값을 돌려준다. 비었거나 예시 자리표시(콘토소)면
    company-profile 회사명(영문 우선)에서 파생한 기본값으로 대체한다.
    → 새 조직이 branding.yaml 을 손대지 않아도 자기 회사명이 헤더에 나온다.
    """
    try:
        pack = load_pack("branding")
    except DomainPackError:
        pack = {}
    val = (pack.get(key) or "").strip()
    if val and not any(tok in val.lower() for tok in _PLACEHOLDER_TOKENS):
        return val

    ko, en = company_names()
    name = en or ko or "Company"
    upper = name.upper()
    return {
        "org_name": ko or name,
        "org_name_en": en or name,
        "dept": "Intelligence",
        "html_header_pr": f"{upper} · PR MONITORING",
        "html_header_newsletter": f"{upper} · INDUSTRY INSIGHT",
        "html_footer": f"{name} · Intelligence",
    }.get(key, name)
