#!/usr/bin/env python3
"""
Batch article extraction — runs the article-extractor skill on all URLs
from urls-{date}.json. Saves a combined results file (extracted-{date}.json).

기존에는 URL 1건당 extract.py 를 subprocess 로 띄웠으나 (기사 50건 = 인터프리터
50회 기동), extract.py 의 extract() 함수를 in-process 로 직접 호출한다.
extract.py 내부 네트워크 호출은 전부 자체 timeout (15~30s) 보유.
"""
import sys
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ (lib import)
from lib.common import RAW_DIR, PROCESSED_DIR, is_blog, load_json, save_json
from prmonitor import paths

EXTRACT_SCRIPT = paths.SKILLS_DIR / "article-extractor" / "extract.py"


def _load_extractor():
    """article-extractor 스킬의 extract.py 를 모듈로 로드."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("article_extractor", EXTRACT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    urls_file = RAW_DIR / f"urls-{today}-filtered.json"
    if not urls_file.exists():
        urls_file = RAW_DIR / f"urls-{today}.json"

    if not urls_file.exists():
        print(f"ERROR: urls-{today}[-filtered].json not found", file=sys.stderr)
        sys.exit(1)

    data = load_json(urls_file)

    # Flatten clusters to articles, pick representative (first) from each cluster
    articles = []
    for cluster in data.get("clusters", []):
        arts = cluster.get("articles", [])
        if arts:
            rep = arts[0]
            rep["_cluster_size"] = len(arts)
            articles.append(rep)

    print(f"Processing {len(articles)} representative articles from {len(data.get('clusters', []))} clusters", file=sys.stderr)

    results = []
    success = 0
    failed = 0

    # 블로그/비공식 채널 사전 필터 — 추출 토큰 낭비 방지
    pre_count = len(articles)
    articles = [a for a in articles if not is_blog(a.get("url", ""))]
    skipped_blogs = pre_count - len(articles)
    if skipped_blogs:
        print(f"Skipped {skipped_blogs} blog URLs (pre-extraction filter)", file=sys.stderr)

    extractor = _load_extractor()

    def extract_one(url: str) -> dict:
        try:
            return extractor.extract(url)
        except Exception as e:
            return {"url": url, "error": str(e)[:200]}

    # Process with thread pool (I/O bound — network requests)
    max_workers = min(10, len(articles)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_article = {}
        for art in articles:
            url = art.get("url", "")
            if not url:
                continue
            future = executor.submit(extract_one, url)
            future_to_article[future] = art

        for i, future in enumerate(as_completed(future_to_article)):
            art = future_to_article[future]
            result = future.result()

            # Merge original metadata
            result["_original_title"] = art.get("title", "")
            result["_source_name"] = art.get("source_name", "")
            result["_published"] = art.get("published", "")
            result["_language"] = art.get("language", "")
            result["_source_query"] = art.get("source_query", "")
            result["_source_tier"] = art.get("source_tier", "")
            result["_cluster_size"] = art.get("_cluster_size", 1)

            if "error" in result:
                failed += 1
                print(f"  [{i+1}/{len(articles)}] FAIL: {art.get('title', '?')[:60]} — {result['error']}", file=sys.stderr)
            else:
                success += 1
                wc = result.get("word_count", 0)
                print(f"  [{i+1}/{len(articles)}] OK: {art.get('title', '?')[:60]} ({wc} words)", file=sys.stderr)

            results.append(result)

    # Save combined results
    output_file = PROCESSED_DIR / f"extracted-{today}.json"
    save_json(output_file, {
        "date": today,
        "total": len(articles),
        "success": success,
        "failed": failed,
        "articles": results
    })

    print(f"\n✅ Done: {success} success, {failed} failed. Saved to {output_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
