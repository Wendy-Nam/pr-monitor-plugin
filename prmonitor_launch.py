#!/usr/bin/env python3
"""Path-robust launcher for the prmonitor CLI.

Hooks and commands run from an arbitrary cwd, and at first-run the plugin's venv
does not exist yet — so this launcher uses the *system* interpreter, inserts the
plugin root on sys.path, and delegates to ``prmonitor.__main__``. The dispatcher
then bootstraps the venv for steps that need dependencies.

Invoked as:  python3 "${CLAUDE_PLUGIN_ROOT}/prmonitor_launch.py" <subcommand> ...
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prmonitor.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
