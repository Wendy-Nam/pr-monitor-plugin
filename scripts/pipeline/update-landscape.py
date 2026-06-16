#!/usr/bin/env python3
"""Step 9: competitor-landscape.yaml 자동 갱신 (LLM 호출 없음)

입력: data/processed/newsletter-briefing-{date}.json (landscape_update_points)
대상: data/self-context/competitor-landscape.yaml

동작:
  1. update point 의 competitor 를 기준선 키로 해석 (키/aliases/company-profile aliases)
  2. 같은 dimension 의 기존 update 는 새 정보로 교체 (차원당 최신 1건 유지)
  3. 30일 초과 update 는 dimension 별로 병합 압축 (archive)
  4. themes / self_implications / baseline 은 절대 건드리지 않음 (사람 영역)

사용:
  python3 scripts/pipeline/update-landscape.py              # 오늘
  python3 scripts/pipeline/update-landscape.py 2026-06-15   # 지정일
  python3 scripts/pipeline/update-landscape.py --render-md  # 사람용 마크다운 출력
"""

from __future__ import annotations

import json
import sys
from datetime import date as Date
from pathlib import Path

import yaml

# Plugin path layer (3-root model; dev fallback collapses all roots to repo root).
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths, domainpack

# self-context lives under PROJECT_DIR (was PROJECT_ROOT/data/self-context, ref line 29).
LANDSCAPE = paths.SELF_CONTEXT_DIR / "competitor-landscape.yaml"
COMPACT_AGE_DAYS = 30

# 카테고리 라벨은 도메인팩(categories.yaml)에서 읽는다 (구: 하드코딩 dict).
_cats = domainpack.load_pack("categories")
CAT_LABELS = {
    cid: (_cats.get("categories", {}).get(cid) or {}).get("label_ko", cid)
    for cid in _cats.get("order", [])
}


def load_landscape() -> dict:
    return yaml.safe_load(LANDSCAPE.read_text(encoding="utf-8"))


def save_landscape(data: dict) -> None:
    LANDSCAPE.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=100),
        encoding="utf-8")


def build_alias_map(data: dict) -> dict[str, str]:
    """alias(소문자) → 기준선 키. landscape aliases + company-profile aliases."""
    amap: dict[str, str] = {}
    for key, comp in data.get("competitors", {}).items():
        amap[key.lower()] = key
        for a in (comp or {}).get("aliases", []) or []:
            amap[a.lower()] = key

    profile_path = paths.CONFIG_DIR / "company-profile.yaml"  # was PROJECT_ROOT/config (ref line 60)
    if profile_path.exists():
        try:
            profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            for c in profile.get("competitors", []):
                names = [c.get("name", "")] + (c.get("aliases", []) or [])
                # profile 그룹 중 하나라도 기준선 키에 닿으면 전체를 그 키로
                target = next((amap[n.lower()] for n in names if n.lower() in amap), None)
                if target:
                    for n in names:
                        amap.setdefault(n.lower(), target)
        except Exception:
            pass
    return amap


def parse_iso(s: str) -> Date | None:
    try:
        return Date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def apply_points(data: dict, points: list[dict]) -> int:
    amap = build_alias_map(data)
    valid_dims = set(data.get("dimensions", {}))
    applied = 0

    for p in points:
        name = (p.get("competitor") or "").strip()
        info = (p.get("new_info") or "").strip()
        dim = (p.get("dimension") or "").strip()
        if not name or not info:
            continue
        key = amap.get(name.lower())
        if not key:
            print(f"  ⚠ 경쟁사 '{name}' 기준선에 없음 — skip")
            continue
        if dim and dim not in valid_dims:
            print(f"  ⚠ {key}: 정의되지 않은 dimension '{dim}' — etc 로 기록")
            dim = ""

        comp = data["competitors"].setdefault(key, {})
        updates = comp.setdefault("updates", [])
        entry = {
            "dimension": dim or "etc",
            "info": info,
            "source": (p.get("source") or "").strip(),
            "date": (p.get("date") or Date.today().isoformat())[:10],
        }
        # 동일 info 중복 방지
        if any(u.get("info") == info for u in updates):
            print(f"  · {key}: 동일 내용 이미 존재 — skip")
            continue
        # 같은 dimension 은 최신 1건 유지 (기존 건은 archive 로 이동)
        for old in [u for u in updates if u.get("dimension") == entry["dimension"]]:
            updates.remove(old)
            comp.setdefault("archive", []).append(old)
        updates.append(entry)
        applied += 1
        print(f"  ✓ {key}/{entry['dimension']}: {info[:60]}")

    return applied


def compact(data: dict) -> int:
    """updates 중 30일 초과 건 + archive 를 dimension 별 1줄로 병합."""
    today = Date.today()
    compacted = 0
    for comp in data.get("competitors", {}).values():
        if not comp:
            continue
        updates = comp.get("updates", []) or []
        keep, old = [], list(comp.get("archive", []) or [])
        n_pre_archive = len(old)
        for u in updates:
            d = parse_iso(u.get("date", ""))
            if d and (today - d).days > COMPACT_AGE_DAYS:
                old.append(u)
            else:
                keep.append(u)
        if not old:
            continue
        by_dim: dict[str, list[dict]] = {}
        for u in old:
            by_dim.setdefault(u.get("dimension", "etc"), []).append(u)
        merged = []
        for dim, items in by_dim.items():
            dates = sorted(filter(None, (parse_iso(i.get("date", "")) for i in items)))
            span = (f"{dates[0].isoformat()}~{dates[-1].isoformat()}"
                    if len(dates) > 1 else (dates[0].isoformat() if dates else "이전"))
            merged.append({
                "dimension": dim,
                "info": " / ".join(i.get("info", "") for i in items),
                "source": "병합" if len(items) > 1 else items[0].get("source", ""),
                "date": span,
            })
        comp["updates"] = keep
        comp["archive"] = merged
        compacted += len(old) - n_pre_archive
    return compacted


def render_md(data: dict) -> str:
    """사람용 마크다운 렌더 (읽기 전용 산출물 — 진실 원천은 YAML)."""
    meta = data.get("meta", {})
    out = ["# 경쟁사 기준선 (Competitor Landscape)",
           f"> 출처: {meta.get('source', '')}",
           f"> 최종 업데이트: {meta.get('updated', '')}", "",
           f"> {meta.get('note', '')}", ""]
    by_cat: dict[str, list[tuple[str, dict]]] = {}
    for key, comp in data.get("competitors", {}).items():
        by_cat.setdefault((comp or {}).get("category", "기타"), []).append((key, comp or {}))
    for cat, comps in by_cat.items():
        out.append(f"## {CAT_LABELS.get(cat, cat)}")
        for key, comp in comps:
            base = comp.get("baseline", "").strip()
            out.append(f"- **{key}**: {base}" if base else f"- **{key}**:")
            for u in (comp.get("updates", []) or []) + (comp.get("archive", []) or []):
                src = f" ({u.get('source', '')}, {u.get('date', '')})" if u.get("source") else f" ({u.get('date', '')})"
                out.append(f"  - [{u.get('dimension', '')}] {u.get('info', '')}{src}")
        out.append("")
    if data.get("themes"):
        out += ["## 경쟁 구도 관통 주제", data["themes"].strip(), ""]
    if data.get("self_implications"):
        out += ["## 자사 대비 시사", data["self_implications"].strip(), ""]
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    data = load_landscape()

    if "--render-md" in args:
        print(render_md(data))
        return

    date_str = args[0] if args else Date.today().isoformat()
    briefing_path = paths.BRIEFING_DIR / f"newsletter-briefing-{date_str}.json"
    if not briefing_path.exists():
        print(f"No briefing for {date_str}, landscape not modified")
        return
    points = json.loads(briefing_path.read_text(encoding="utf-8")).get("landscape_update_points", [])
    if not points:
        print("No update points, landscape not modified")
        return

    applied = apply_points(data, points)
    n_compacted = compact(data)
    if applied or n_compacted:
        data.setdefault("meta", {})["updated"] = Date.today().isoformat()
        save_landscape(data)
    print(f"✓ landscape: {applied}건 반영, {n_compacted}건 압축")


if __name__ == "__main__":
    main()
