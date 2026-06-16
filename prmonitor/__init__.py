"""PR Monitor — Claude Code plugin engine package.

Domain-agnostic news-automation pipeline. Org-specific knowledge lives in the
domain pack under ``${CLAUDE_PROJECT_DIR}/config`` (see docs/specs).
"""
__version__ = "0.5.2"


class PrMonitorError(RuntimeError):
    """Base for all engine errors — lets callers ``except PrMonitorError`` catch
    any plugin-originated failure (venv bootstrap, domain-pack load, …) while
    still subclassing RuntimeError for legacy ``except RuntimeError`` paths."""
