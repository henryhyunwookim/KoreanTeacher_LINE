"""Local Korean Information and Web Search Aggregation Module.

=============================================================================
PURPOSE:
    Provides autonomous tool-calling search capabilities for the Gemini AI tutor.
    Aggregates native South Korean search engines (Naver and Kakao/Daum) alongside
    Google Custom Search to provide accurate, up-to-date recommendations for:
      - Local Korean places, cafes, and restaurants (맛집)
      - Travel itineraries, subway navigation, and cultural etiquette
      - Trending slang (신조어) and pop culture references
      - Factual grammar explanations and colloquial usage

DESIGN RATIONALE:
    Standard global search indexes often miss fast-evolving Korean local blog posts
    and cafe reviews. Naver Blog and Kakao/Daum provide rich, native Korean-language
    ground truth. Each search provider fails gracefully without throwing exceptions,
    allowing Gemini to proceed with partial results if any single provider is offline.
=============================================================================
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
import httpx

from app.config import get_setting

logger = logging.getLogger(__name__)


# =============================================================================
# Naver Search Integration (Blog & Webkr)
# =============================================================================

def search_naver(query: str) -> str:
    """Queries Naver Search API for blog posts and Korean web results.

    Args:
        query: The search keywords in Korean or Japanese.

    Returns:
        Formatted summary of Naver results or an informative notice if unconfigured.
    """
    client_id = get_setting("NAVER_CLIENT_ID", ["naver-client-id"])
    client_secret = get_setting("NAVER_CLIENT_SECRET", ["naver-client-secret"])

    if not client_id or not client_secret:
        return "Naver API keys not configured. (Please configure NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)"

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret
    }
    results: List[str] = []

    # Step 1: Query Naver Blog endpoint (valuable for local reviews and personal tips)
    try:
        with httpx.Client() as client:
            r = client.get(
                "https://openapi.naver.com/v1/search/blog.json",
                headers=headers,
                params={"query": query, "display": 3},
                timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                items = data.get("items", [])
                for item in items:
                    title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                    link = item.get("link", "")
                    description = item.get("description", "").replace("<b>", "").replace("</b>", "")
                    results.append(f"[Naver Blog] Title: {title}\nLink: {link}\nDescription: {description}")
            else:
                results.append(f"[Naver Blog Error] HTTP status {r.status_code}: {r.text}")
    except Exception as e:
        results.append(f"[Naver Blog Exception] {e}")

    # Step 2: Query Naver Webkr endpoint (authoritative Korean web documents)
    try:
        with httpx.Client() as client:
            r = client.get(
                "https://openapi.naver.com/v1/search/webkr.json",
                headers=headers,
                params={"query": query, "display": 3},
                timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                items = data.get("items", [])
                for item in items:
                    title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                    link = item.get("link", "")
                    description = item.get("description", "").replace("<b>", "").replace("</b>", "")
                    results.append(f"[Naver Web] Title: {title}\nLink: {link}\nDescription: {description}")
            else:
                results.append(f"[Naver Web Error] HTTP status {r.status_code}: {r.text}")
    except Exception as e:
        results.append(f"[Naver Web Exception] {e}")

    return "\n\n".join(results)


# =============================================================================
# Kakao / Daum Search Integration (Web & Blog)
# =============================================================================

def search_kakao(query: str) -> str:
    """Queries Kakao / Daum Open API for web documents and blog posts.

    Args:
        query: The search keywords in Korean or Japanese.

    Returns:
        Formatted summary of Kakao search results or an informative notice.
    """
    rest_api_key = get_setting("KAKAO_REST_API_KEY", ["kakao-rest-api-key"])
    if not rest_api_key:
        return "Kakao API key not configured. (Please configure KAKAO_REST_API_KEY)"

    headers = {
        "Authorization": f"KakaoAK {rest_api_key}"
    }
    results: List[str] = []

    # Step 1: Query Kakao Web Search endpoint
    try:
        with httpx.Client() as client:
            r = client.get(
                "https://dapi.kakao.com/v2/search/web",
                headers=headers,
                params={"query": query, "size": 3},
                timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                documents = data.get("documents", [])
                for doc in documents:
                    title = doc.get("title", "").replace("<b>", "").replace("</b>", "")
                    link = doc.get("url", "")
                    contents = doc.get("contents", "").replace("<b>", "").replace("</b>", "")
                    results.append(f"[Kakao Web] Title: {title}\nLink: {link}\nDescription: {contents}")
            else:
                results.append(f"[Kakao Web Error] HTTP status {r.status_code}: {r.text}")
    except Exception as e:
        results.append(f"[Kakao Web Exception] {e}")

    # Step 2: Query Kakao Blog Search endpoint
    try:
        with httpx.Client() as client:
            r = client.get(
                "https://dapi.kakao.com/v2/search/blog",
                headers=headers,
                params={"query": query, "size": 3},
                timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                documents = data.get("documents", [])
                for doc in documents:
                    title = doc.get("title", "").replace("<b>", "").replace("</b>", "")
                    link = doc.get("url", "")
                    contents = doc.get("contents", "").replace("<b>", "").replace("</b>", "")
                    results.append(f"[Kakao Blog] Title: {title}\nLink: {link}\nDescription: {contents}")
            else:
                results.append(f"[Kakao Blog Error] HTTP status {r.status_code}: {r.text}")
    except Exception as e:
        results.append(f"[Kakao Blog Exception] {e}")

    return "\n\n".join(results)


# =============================================================================
# Google Custom Search Integration (Global Coverage)
# =============================================================================

def search_google(query: str) -> str:
    """Queries Google Custom Search JSON API for broader international results.

    Args:
        query: The search query string.

    Returns:
        Formatted summary of top 5 Google search snippets, or empty string if unconfigured.
    """
    api_key = get_setting("GOOGLE_SEARCH_API_KEY", ["google-search-api-key"])
    cx = get_setting("GOOGLE_SEARCH_CX", ["google-search-cx"])
    if not api_key or not cx:
        return ""

    results: List[str] = []
    try:
        with httpx.Client() as client:
            r = client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cx, "q": query, "num": 5},
                timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                items = data.get("items", [])
                for item in items:
                    title = item.get("title", "")
                    link = item.get("link", "")
                    snippet = item.get("snippet", "")
                    results.append(f"[Google] Title: {title}\nLink: {link}\nDescription: {snippet}")
            else:
                results.append(f"[Google Error] HTTP status {r.status_code}")
    except Exception as e:
        results.append(f"[Google Exception] {e}")

    return "\n\n".join(results)


# =============================================================================
# Aggregated Tool Entrypoint for Gemini Function Calling
# =============================================================================

def search_naver_and_kakao(query: str) -> str:
    """Searches Naver, Kakao/Daum, and Google for real-time Korean information.

    Use this tool whenever the user's query involves local Korean information, places,
    restaurants (맛집), travel recommendations, culture, history, food, news, or any
    factual question that requires up-to-date regional data.

    Args:
        query: The search query in Korean or Japanese.

    Returns:
        Consolidated multi-engine search results.
    """
    # Provider requests are independent; keep the pool small and preserve output order.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="search") as executor:
        naver_future = executor.submit(search_naver, query)
        kakao_future = executor.submit(search_kakao, query)
        google_future = executor.submit(search_google, query)
        naver_res = naver_future.result()
        kakao_res = kakao_future.result()
        google_res = google_future.result()

    output: List[str] = []

    if "not configured" not in naver_res:
        output.append("=== Naver Search Results ===")
        output.append(naver_res)
    else:
        output.append(naver_res)

    if "not configured" not in kakao_res:
        output.append("=== Kakao Search Results ===")
        output.append(kakao_res)
    else:
        output.append(kakao_res)

    if google_res:
        output.append("=== Google Search Results ===")
        output.append(google_res)

    return "\n\n".join(output)
