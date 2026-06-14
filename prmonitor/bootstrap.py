"""Cross-platform venv bootstrap — replaces ``ensure_venv()`` from common.sh.

The original bash bootstrap was the single biggest Windows blocker:
  - assumed a system ``python3`` on PATH (Windows has ``py``/``python``, and a
    ``python3.exe`` Store-alias stub that hangs);
  - hardcoded the POSIX interpreter path ``.venv/bin/python3`` (Windows venvs use
    ``.venv\\Scripts\\python.exe``);
  - wrote the venv into the (now read-only) plugin dir.

This module locates a usable interpreter portably, creates the venv under the
writable ``${CLAUDE_PLUGIN_DATA}`` (so it survives plugin updates), and installs
requirements once. Idempotent: re-running is a no-op when the venv is healthy.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import paths


class BootstrapError(RuntimeError):
    pass


def find_system_python() -> list[str]:
    """Return an argv prefix for a usable system Python 3, or raise.

    Tries, in order: the current interpreter, then ``python3``/``python``/``py``
    on PATH. On Windows ``py -3`` is the reliable launcher. Skips the Windows
    Store-alias stub (it has no real prefix).
    """
    # The interpreter running this code is always valid (unless it IS the stub).
    if sys.executable and "WindowsApps" not in sys.executable:
        return [sys.executable]

    candidates: list[list[str]] = []
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:
            candidates.append([found])
    if shutil.which("py"):
        candidates.append(["py", "-3"])

    for argv in candidates:
        try:
            out = subprocess.run(
                [*argv, "-c", "import sys; print(sys.version_info[0])"],
                capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip() == "3":
                return argv
        except (OSError, subprocess.SubprocessError):
            continue
    raise BootstrapError(
        "사용 가능한 Python 3을 찾지 못했습니다. python.org에서 Python 3.10+ 를 설치하세요 "
        "(Windows: 설치 시 'Add to PATH' 체크)."
    )


def venv_healthy() -> bool:
    """The venv exists and its interpreter imports a core dependency (yaml)."""
    py = paths.venv_python()
    if not py.exists():
        return False
    try:
        r = subprocess.run([str(py), "-c", "import yaml, trafilatura"],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_venv(*, quiet: bool = True) -> Path:
    """Ensure the PLUGIN_DATA venv exists with deps installed. Returns interpreter path."""
    py = paths.venv_python()
    if venv_healthy():
        return py

    paths.VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
    system_py = find_system_python()

    if not py.exists():
        if not quiet:
            print("Python 가상환경 생성 중...", file=sys.stderr)
        _run([*system_py, "-m", "venv", str(paths.VENV_DIR)],
             "가상환경 생성 실패 (python -m venv).")

    req = paths.REQUIREMENTS
    if not req.exists():
        raise BootstrapError(f"requirements.txt 없음: {req}")
    if not quiet:
        print("의존성 설치 중...", file=sys.stderr)
    _run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
          "-r", str(req)], "의존성 설치 실패 (pip install).")

    if not venv_healthy():
        raise BootstrapError("venv 부트스트랩 후에도 핵심 의존성 import 실패.")
    return py


def _run(argv: list[str], err_msg: str) -> None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        raise BootstrapError(f"{err_msg} ({e})") from e
    if r.returncode != 0:
        raise BootstrapError(f"{err_msg}\n{r.stderr.strip()[:500]}")


if __name__ == "__main__":
    p = ensure_venv(quiet=False)
    print(p)
