"""Google News RSS redirect URL → 실제 소스 URL 해석.

Google News RSS 의 `link` 필드는 `https://news.google.com/rss/articles/<base64>?oc=5`
형태의 리다이렉트 URL 이며, 2024년 후반부터 직접 HTTP fetch 시 HTTP 451 로 차단된다.
이 때문에 downstream 의 article-extractor 가 본문 추출에 실패한다.

해결: `googlenewsdecoder` 로 수집 단계에서 실제 URL 해석해 저장.

최적화:
 - 병렬 해석 (ThreadPoolExecutor)
 - 일별 캐시 (`data/cache/gnews-resolutions.json`) — 같은 URL 재해석 방지

사용:
    from lib.gnews_resolver import resolve_urls_in_articles
    stats = resolve_urls_in_articles(articles, max_workers=5)
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

# Plugin re-anchor: derive paths from the prmonitor package (3-root model) rather
# than self-deriving a root via Path(__file__).parents[2], which resolves to the
# read-only PLUGIN_ROOT under the plugin layout. In dev (no CLAUDE_* env vars set)
# paths.* collapse to the repo root, so behaviour is byte-for-byte unchanged.
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prmonitor import paths


GNEWS_PREFIXES = (
    "https://news.google.com/rss/articles/",
    "https://news.google.com/articles/",
    "https://news.google.com/read/",
)

# 캐시: 한 번 해석된 URL 은 파일에 영구 기록. Google News URL 은 기사 고유 ID 를
# 담고 있어 내용이 바뀌지 않는다. 일별 파일이 아닌 전역 파일 하나로 충분.
# 원본(ref scripts/lib/gnews_resolver.py:34): Path(__file__).resolve().parents[2]
#   / "data" / "cache" / "gnews-resolutions.json" — disposable cache 이므로
#   PLUGIN_DATA 하위 paths.CACHE_DIR(= PLUGIN_DATA/data/cache) 로 재anchor.
_CACHE_PATH = paths.CACHE_DIR / "gnews-resolutions.json"


def is_gnews_redirect(url: str) -> bool:
    return url.startswith(GNEWS_PREFIXES)


@dataclass
class ResolveStats:
    total: int = 0
    skipped_non_gnews: int = 0
    cache_hits: int = 0
    resolved: int = 0
    failed: int = 0
    aborted_early: bool = False

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "skipped_non_gnews": self.skipped_non_gnews,
            "cache_hits": self.cache_hits,
            "resolved": self.resolved,
            "failed": self.failed,
            "aborted_early": self.aborted_early,
        }


def _load_cache() -> dict[str, str]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _resolve_single(url: str, interval_seconds: float) -> str | None:
    """단일 URL 해석. 성공 시 실제 URL, 실패 시 None.
    interval_seconds 는 googlenewsdecoder 내부 재시도 간격.
    """
    try:
        from googlenewsdecoder import new_decoderv1
    except ImportError:
        return None

    try:
        result = new_decoderv1(url, interval=interval_seconds)
    except Exception:
        return None

    if not isinstance(result, dict) or not result.get("status"):
        return None
    decoded = result.get("decoded_url")
    if isinstance(decoded, str) and decoded.startswith("http"):
        return decoded
    return None


def resolve_urls_in_articles(
    articles: list,
    interval_seconds: float = 0.3,
    max_consecutive_failures: int = 8,
    max_workers: int = 5,
) -> ResolveStats:
    """Article 리스트의 Google News redirect URL 을 병렬 + 캐시로 해석.

    전략:
      1. 캐시 적중분 → 즉시 치환 (네트워크 0회)
      2. 미적중분 → ThreadPoolExecutor 로 동시 해석
      3. 결과 캐시에 추가 저장

    interval_seconds 는 decoder 내부 간격 (각 워커 독립). 기존 1.0 → 0.3 (병렬이므로 낮춤).
    max_workers=5 는 Google 레이트리밋 고려한 안전치.
    """
    stats = ResolveStats()
    stats.total = len(articles)

    try:
        import googlenewsdecoder  # noqa: F401
    except ImportError:
        print("  WARN googlenewsdecoder 미설치 — Google News URL 해석 skip "
              "(uv pip install googlenewsdecoder)", file=sys.stderr)
        for art in articles:
            if is_gnews_redirect(getattr(art, "url", "")):
                stats.failed += 1
            else:
                stats.skipped_non_gnews += 1
        return stats

    cache = _load_cache()

    # 1단계: 캐시 적중 즉시 치환 + 미적중 목록 수집
    pending: list = []  # [(art, url), ...]
    for art in articles:
        url = getattr(art, "url", "")
        if not is_gnews_redirect(url):
            stats.skipped_non_gnews += 1
            continue
        if url in cache:
            art.url = cache[url]
            stats.cache_hits += 1
            continue
        pending.append((art, url))

    # 2단계: 병렬 해석
    if pending:
        consecutive_failures = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_to_item = {
                ex.submit(_resolve_single, url, interval_seconds): (art, url)
                for art, url in pending
            }
            for fut in as_completed(fut_to_item):
                art, url = fut_to_item[fut]
                resolved = fut.result()
                if resolved:
                    art.url = resolved
                    cache[url] = resolved
                    stats.resolved += 1
                    consecutive_failures = 0
                else:
                    stats.failed += 1
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        # 나머지 futures 는 그대로 두되 실패로 카운트 (동시 실행 중이라
                        # 완전 차단하기 어려움 — 통계만 기록)
                        stats.aborted_early = True

    if stats.resolved:
        _save_cache(cache)

    return stats
