---
name: article-extractor
description: 뉴스 URL 로부터 본문과 메타데이터를 높은 정확도로 추출한다. Trafilatura 기본, 네이버 뉴스는 CSS 셀렉터 특화, 실패 시 Jina Reader API 폴백. 광고·메뉴·관련기사를 제거하고 깨끗한 텍스트만 반환. 2024년 벤치마크 기준 Trafilatura 는 F1 0.945 로 1위.
---

# Article Extractor Skill

뉴스 URL → 깨끗한 본문 + 메타데이터 추출.

## 사용법

```bash
python3 ${CLAUDE_SKILL_DIR}/extract.py <URL> [--output /path/to/output.json]
```

## 왜 이 skill 이 중요한가

LLM 은 **광고/메뉴가 섞인 원본 HTML 을 주면 엉뚱한 걸 요약** 한다. 
본문만 깨끗하게 뽑아주는 것이 환각 저감의 가장 큰 지렛대다 (정확도 향상의 50%+ 기여).

## 추출 전략 (우선순위)

### 1. 네이버 뉴스 특화 (`n.news.naver.com`)
- CSS 셀렉터 `#dic_area` 로 본문
- `.media_end_head_title` 로 제목
- `.media_end_head_info_datestamp_time` 로 발행일
- 가장 정확, 가장 빠름

### 2. Trafilatura (범용)
- 대부분의 뉴스 사이트에 대해 F1 0.945 (Scrapinghub 2024 벤치마크)
- 한국어 포함 다국어 지원
- 메타데이터 동시 추출 (저자, 날짜)

### 3. Jina Reader API 폴백
- 위 둘이 실패하면 `https://r.jina.ai/<URL>` 호출
- 무료 티어로 사용 가능
- JS 렌더링 필요한 사이트도 처리

## 출력 스키마

```json
{
  "url": "...",
  "fetched_at": "2026-04-20T09:30:00+09:00",
  "title": "...",
  "author": null,
  "published_date": "2026-04-19",
  "source_name": "연합뉴스",
  "language": "ko",
  "full_text": "...",
  "paragraphs": [
    {"idx": 1, "text": "..."},
    {"idx": 2, "text": "..."}
  ],
  "word_count": 847,
  "extraction_method": "naver_selector | trafilatura | jina_reader"
}
```

## 실패 처리

- HTTP 4xx/5xx → `{"error": "http_error", "status": N}` 반환 + exit code 1
- 본문 < 500자 → `{"error": "too_short", "length": N}` (광고성/삭제된 기사 의심)
- 인코딩 실패 → `{"error": "encoding"}`
- Paywall 감지 → `{"error": "paywall"}`

## 설치 의존성

처음 1회만:
```bash
pip install trafilatura requests chardet --break-system-packages
```

Jina Reader 는 API 키 없이 무료 사용 (레이트리밋 있음).
