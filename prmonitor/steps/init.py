"""First-run scaffolding — invoked by the SessionStart hook and `prmonitor init`.

Idempotent. Never writes into the read-only plugin root. Creates the writable
skeleton under PROJECT_DIR / PLUGIN_DATA, seeds missing config from bundled
templates, and ensures the venv exists. Safe to run every session.
"""
from __future__ import annotations

import shutil
import sys

from .. import paths


def run(args=None) -> int:
    paths.ensure_dirs()

    seeded: list[str] = []
    if paths.CONFIG_TEMPLATES.is_dir():
        for tmpl in sorted(paths.CONFIG_TEMPLATES.glob("*.yaml")):
            # Never seed a secrets file; that path goes through userConfig→keychain.
            if tmpl.name == "delivery.yaml":
                continue
            dest = paths.CONFIG_DIR / tmpl.name
            if not dest.exists():
                shutil.copy2(tmpl, dest)
                seeded.append(dest.name)

    first_run = not paths.INIT_MARKER.exists()
    if first_run:
        paths.INIT_MARKER.write_text(
            "pr-monitor initialized\n", encoding="utf-8"
        )

    # Best-effort venv bootstrap; don't fail the session if Python is missing —
    # surface a friendly note instead so a chat session can still start.
    try:
        from .. import bootstrap
        if not bootstrap.venv_healthy():
            bootstrap.ensure_venv(quiet=True)
    except Exception as e:  # noqa: BLE001 - SessionStart must not crash the session
        print(f"[prmonitor init] venv 준비 보류: {e}", file=sys.stderr)

    if first_run or seeded:
        msg = "[prmonitor] 초기화 완료"
        if seeded:
            msg += f" — config 시드: {', '.join(seeded)}"
        print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
