"""Common library — Python port of scripts/lib/common.sh.

Owns what every pipeline step shares: config reads, the collection-window
policy, retention cleanup, email dispatch, and small guards. Path resolution is
delegated entirely to :mod:`prmonitor.paths` (the three-root keystone), so this
module has no notion of "the one flat root" the bash version assumed.

Replaces these common.sh functions: pipeline_cfg, resolve_hours, count_urls,
require_file, send_html_email, cleanup_retention. (ensure_venv → bootstrap.py.)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import paths

# Re-export the canonical dirs so step-scripts can `from prmonitor.common import CONFIG_DIR`.
PROJECT_DIR = paths.PROJECT_DIR
CONFIG_DIR = paths.CONFIG_DIR
RAW_DIR = paths.RAW_DIR
PROCESSED_DIR = paths.PROCESSED_DIR
OUTPUT_DIR = paths.OUTPUT_DIR
NEWSLETTER_OUTPUT_DIR = paths.NEWSLETTER_OUTPUT_DIR
PR_OUTPUT_DIR = paths.PR_OUTPUT_DIR
CACHE_DIR = paths.CACHE_DIR
SELF_CONTEXT_DIR = paths.SELF_CONTEXT_DIR
LOGS_DIR = paths.LOGS_DIR


# ── IO helpers (carried over from the original common.py) ─────────────────────
def load_yaml(path: Path | str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path | str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path | str, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


BLOG_DOMAINS = [
    "tistory.com", "blog.naver.com", "brunch.co.kr",
    "medium.com/@", "velog.io", "notion.so",
]


def is_blog(url: str) -> bool:
    return any(bd in url for bd in BLOG_DOMAINS)


# ── logging (ANSI-free; safe on Windows cmd) ──────────────────────────────────
def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_stamp()}] {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"[{_stamp()}] OK {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[{_stamp()}] WARN {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"[{_stamp()}] ERROR {msg}", file=sys.stderr)


# ── config policy (was pipeline_cfg / resolve_hours) ──────────────────────────
def pipeline_cfg(pipeline: str, key: str, default):
    """Read config/pipelines.yaml -> <pipeline>.<key>, falling back to default."""
    try:
        cfg = load_yaml(CONFIG_DIR / "pipelines.yaml")
        val = (cfg.get(pipeline) or {}).get(key)
        return default if val is None else val
    except Exception:
        return default


def resolve_hours(pipeline: str, *, today: datetime | None = None) -> int:
    """Collection window in hours; Monday uses monday_hours (weekend coverage)."""
    base = int(pipeline_cfg(pipeline, "hours", 24))
    monday = int(pipeline_cfg(pipeline, "monday_hours", base))
    now = today or datetime.now()
    return monday if now.weekday() == 0 else base  # Monday == 0


def count_urls(path: Path | str) -> int:
    """Count articles in a urls/clusters json (both shapes), 0 on any error."""
    try:
        d = load_json(path)
    except Exception:
        return 0
    if isinstance(d, dict) and "clusters" in d:
        return sum(c.get("article_count", 1) for c in d["clusters"])
    if isinstance(d, dict) and "urls" in d:
        return len(d["urls"])
    return 0


def require_file(path: Path | str, message: str) -> None:
    if not Path(path).is_file():
        err(message)
        raise SystemExit(1)


# ── email (was send_html_email) ───────────────────────────────────────────────
def send_html_email(group: str, subject: str, html: Path | str,
                    attachment: Path | str | None = None) -> bool:
    """Dispatch via scripts/send-email.py using the venv interpreter.

    Secrets come from injected env (userConfig→keychain) in the plugin model;
    recipient/group config from config/delivery.yaml (or recipients.yaml).
    Never raises on send failure — the caller's artifact is already written.
    """
    delivery = CONFIG_DIR / "delivery.yaml"
    if not delivery.is_file():
        log("delivery.yaml 없음 — 이메일 발송 skip")
        return False
    argv = [str(paths.venv_python()),
            str(paths.SCRIPTS_DIR / "send-email.py"),
            "--group", group, "--subject", subject, "--html", str(html)]
    if attachment and Path(attachment).is_file():
        argv += ["--attachment", str(attachment)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        warn(f"이메일 발송 실행 실패 (산출물은 정상) — {e}")
        return False
    if r.returncode == 0:
        ok("이메일 발송 완료")
        return True
    warn("이메일 발송 실패 (산출물은 정상 생성됨) — Azure 인증 확인")
    return False


# ── retention (was cleanup_retention; now three-root aware) ───────────────────
def cleanup_retention(*, today: datetime | None = None) -> int:
    """Apply retention policy across the (now split) roots. Returns files removed.

      raw/processed (PLUGIN_DATA)        : 7 days
      output *.html/*.xlsx (PROJECT_DIR) : 30 days
      pr-monitoring-*.csv                : delete prior months once monthly roll-up exists
      logs/executions                    : 30 days
      cache, self-context                : untouched
    """
    now = today or datetime.now()
    cur_month = now.strftime("%Y-%m")
    removed = 0

    removed += _purge_older_than([RAW_DIR, PROCESSED_DIR], days=7, now=now)
    removed += _purge_older_than([NEWSLETTER_OUTPUT_DIR, PR_OUTPUT_DIR],
                                 days=30, now=now, suffixes=(".html", ".xlsx"))
    removed += _purge_older_than([LOGS_DIR], days=30, now=now)

    for csv in PR_OUTPUT_DIR.glob("pr-monitoring-*.csv"):
        # name: pr-monitoring-YYYY-MM-DD...csv  → month = YYYY-MM
        parts = csv.name.replace("pr-monitoring-", "").split("-")
        if len(parts) >= 2:
            month = f"{parts[0]}-{parts[1]}"
            monthly = PR_OUTPUT_DIR / f"pr-monthly-{month}.csv"
            if month != cur_month and monthly.is_file():
                csv.unlink(missing_ok=True)
                removed += 1

    if removed:
        log(f"보존 정책 정리: {removed}개 파일 삭제")
    return removed


def _purge_older_than(dirs, *, days: int, now: datetime,
                      suffixes: tuple[str, ...] | None = None) -> int:
    cutoff = now.timestamp() - days * 86400
    removed = 0
    for d in dirs:
        if not Path(d).is_dir():
            continue
        for f in Path(d).rglob("*"):
            if not f.is_file():
                continue
            if suffixes and f.suffix not in suffixes:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    return removed
