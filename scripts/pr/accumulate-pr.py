#!/usr/bin/env python3
"""PR 모니터링 CSV 월별 누적

사용:
  python3 scripts/accumulate-pr.py              # 이번 달 누적
  python3 scripts/accumulate-pr.py 2026-06      # 특정 월 누적
  python3 scripts/accumulate-pr.py --all        # 전체 기간 누적

data/output/pr/pr-monitoring-YYYY-MM-DD.csv 파일들을 읽어
data/output/pr/pr-monthly-YYYY-MM.csv 로 합친다.

중복 제거: 같은 URL은 1건만 유지 (최신 날짜 기준).
인코딩: UTF-8 BOM (엑셀 한국어 호환).
"""

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ (lib import)
from lib.common import CONFIG_DIR, PR_OUTPUT_DIR, load_yaml
PR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 일일 CSV(gen-pr-monitor.py) 헤더와 동일해야 함
HEADER = ["날짜", "매체", "기자", "제목", "언급유형", "톤", "주가관련", "맥락", "URL"]


def load_daily_csvs(month: str | None = None) -> list[dict]:
    """월별 또는 전체 일일 CSV 로드"""
    pattern = "pr-monitoring-*.csv"
    files = sorted(PR_OUTPUT_DIR.glob(pattern))

    rows = []
    seen_urls = set()

    for f in files:
        # 파일명에서 날짜 추출: pr-monitoring-2026-06-05.csv
        stem = f.stem  # pr-monitoring-2026-06-05 or pr-monitoring-2026-06-05-v2
        parts = stem.replace("pr-monitoring-", "").split("-")
        if len(parts) < 3:
            continue
        file_month = f"{parts[0]}-{parts[1]}"

        # 월 필터
        if month and file_month != month:
            continue

        # -v2, -v3 등은 최신 버전만
        # 같은 날짜의 파일이 여러 개면 가장 큰 버전만
        # (단순화: 모두 읽되 URL 기준 dedup)

        try:
            with open(f, encoding="utf-8-sig") as csvf:
                reader = csv.DictReader(csvf)
                for row in reader:
                    url = row.get("URL", "").strip()
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        rows.append(row)
        except Exception as e:
            print(f"WARN: {f.name} 읽기 실패 — {e}")

    return rows


def resolve_media_in_rows(rows: list[dict]) -> list[dict]:
    """Google News 라벨 → 실제 매체명 변환"""
    try:
        mapping_path = CONFIG_DIR / "media-mapping.yaml"
        media_map = load_yaml(mapping_path).get("mapping", {}) if mapping_path.exists() else {}
    except ImportError:
        media_map = {}

    for row in rows:
        url = row.get("URL", "")
        media = row.get("매체", "")

        # "Google News [...]" 패턴 → URL 기반 매체명
        if "Google News" in media or not media:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                if domain.startswith("m."):
                    domain = domain[2:]
                if domain in media_map:
                    row["매체"] = media_map[domain]
                elif domain:
                    row["매체"] = domain
            except Exception as pe:
                print(f"WARN: 매체 파싱 실패 (URL: {url[:60]}) — {pe}", file=sys.stderr)

    return rows


def write_monthly(rows: list[dict], month: str):
    """월별 누적 CSV 저장"""
    if not rows:
        print(f"No data for {month}")
        return

    # 날짜순 정렬
    rows.sort(key=lambda r: r.get("날짜", ""), reverse=True)

    out_path = PR_OUTPUT_DIR / f"pr-monthly-{month}.csv"

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"=== PR Monthly Report ({month}) ===")
    print(f"Articles: {len(rows)}")

    # 톤 분포
    from collections import Counter
    tones = Counter(r.get("톤", "미분류") for r in rows)
    print(f"Tone: {dict(tones)}")

    # 매체 분포
    media = Counter(r.get("매체", "?") for r in rows)
    print(f"Top media: {media.most_common(5)}")

    print(f"Output: {out_path}")


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--all":
            month = None
        else:
            month = arg  # "2026-06" format
    else:
        month = date.today().strftime("%Y-%m")

    rows = load_daily_csvs(month)
    rows = resolve_media_in_rows(rows)

    if month:
        write_monthly(rows, month)
    else:
        # --all: 월별로 분리 저장
        by_month: dict[str, list[dict]] = {}
        for r in rows:
            d = r.get("날짜", "")[:7]  # "2026-06"
            if d:
                by_month.setdefault(d, []).append(r)
        for m, m_rows in sorted(by_month.items()):
            write_monthly(m_rows, m)


if __name__ == "__main__":
    main()
