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
    # SessionStart hook fires in every workspace. Only scaffold if this
    # workspace has already opted in (marker exists) or --force is given
    # (explicit first-time setup via /setup → `init --force`).
    if paths.IS_PLUGIN and not getattr(args, "force", False) and not paths.INIT_MARKER.exists():
        return 0

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
        # 세션은 살리되, 조용히 끝내지 않고 원인+해결책을 명시한다.
        print(
            f"[prmonitor init] Python 가상환경 준비 실패: {e}\n"
            f"  → 리포트 생성에는 Python 3.9+ 가 필요합니다. python.org 에서 설치 후 "
            f"세션을 다시 시작하거나 `/setup` 을 실행하세요. "
            f"(설정 스캐폴딩은 완료됐으니 채팅은 계속 가능합니다.)",
            file=sys.stderr,
        )

    if first_run or seeded:
        msg = "[prmonitor] 초기화 완료"
        if seeded:
            msg += f" — config 시드: {', '.join(seeded)}"
        print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
