#!/usr/bin/env python3
"""setup 가드레일 — 이미 설정된 도메인팩을 실수로 리셋하지 않게.

`/setup` INSTALL(자동·반자동·수동)을 기설정 워크스페이스에서 잘못 실행해도
사용자가 큐레이션한 도메인팩을 말없이 덮지 않도록, 쓰기 전에 이걸로 판정·백업한다.
init(SessionStart)은 이미 빈 파일만 시드해 안전하지만, setup-bootstrap 에이전트는
파일을 쓰므로 이 결정론적 가드를 거친다.

  --check  : config/ 가 사용자 실데이터인지 판정.
             exit 0 = 안전(미설정이거나 번들 예시 그대로 — 덮어도 손실 없음)
             exit 3 = 사용자 실데이터(덮으면 손실) → 호출자는 백업+명시 승인 없이 진행 금지
             stdout 에 company.name 출력.
  --backup : config/ 전체를 config.bak-<timestamp>/ 로 복사(삭제 아님). 경로 출력.

사용:
  python3 scripts/lib/setup-guard.py --check
  python3 scripts/lib/setup-guard.py --backup
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths  # noqa: E402

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml 필요", file=sys.stderr)
    sys.exit(2)


def _company_name(path: Path) -> str:
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str((d.get("company") or {}).get("name") or "").strip()
    except Exception:
        return ""


def is_real_user_data() -> tuple[bool, str]:
    """(실데이터 여부, company.name). 라이브가 없거나, 이름이 비었거나, 번들 예시
    템플릿과 동일하면 '예시 시드'로 보고 False(안전). 다르면 True(사용자 실데이터)."""
    live = paths.CONFIG_DIR / "company-profile.yaml"
    if not live.exists():
        return False, ""
    live_name = _company_name(live)
    if not live_name:
        return False, ""
    tmpl = paths.CONFIG_TEMPLATES / "company-profile.yaml"
    tmpl_name = _company_name(tmpl) if tmpl.exists() else ""
    return (live_name != tmpl_name), live_name


def do_backup() -> Path:
    src = paths.CONFIG_DIR
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    dst = src.parent / f"config.bak-{ts}"
    shutil.copytree(src, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(prog="setup-guard")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backup", action="store_true")
    args = ap.parse_args()

    if args.backup:
        if not paths.CONFIG_DIR.exists():
            print("config/ 없음 — 백업할 것 없음")
            return 0
        dst = do_backup()
        print(f"✓ 백업 생성: {dst}")
        return 0

    # 기본 = --check
    real, name = is_real_user_data()
    if real:
        print(f"사용자 도메인팩 감지: '{name}'. 덮어쓰기 전 --backup + 명시 승인 필요.")
        return 3
    print("안전: 사용자 실데이터 없음(미설정 또는 번들 예시 그대로).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
