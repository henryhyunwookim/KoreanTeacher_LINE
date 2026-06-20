import os
import httpx

def search_naver(query: str) -> str:
    """Query Naver Search API for blog and webkr results."""
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return "Naver API keys not configured. (Please configure NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)"
    
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret
    }
    
    results = []
    
    # 1. Search Blog
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
        results.append(f"[Naver Blog Exception] {str(e)}")
        
    # 2. Search Web
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
        results.append(f"[Naver Web Exception] {str(e)}")
        
    return "\n\n".join(results)

def search_kakao(query: str) -> str:
    """Query Kakao/Daum Search API for web and blog results."""
    rest_api_key = os.environ.get("KAKAO_REST_API_KEY")
    if not rest_api_key:
        return "Kakao API key not configured. (Please configure KAKAO_REST_API_KEY)"
        
    headers = {
        "Authorization": f"KakaoAK {rest_api_key}"
    }
    
    results = []
    
    # 1. Search Web
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
        results.append(f"[Kakao Web Exception] {str(e)}")
        
    # 2. Search Blog
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
        results.append(f"[Kakao Blog Exception] {str(e)}")
        
    return "\n\n".join(results)

def search_naver_and_kakao(query: str) -> str:
    """
    Search Naver and Kakao/Daum search engines to gather local information, travel tips, blog posts, and news about Korea.
    Also performs a Google web search for broader international coverage.
    Use this tool when the user's query involves Korean information, places, restaurants, travel recommendations, culture, history, food, news, or any factual question that requires up-to-date information.
    
    Args:
        query: The search query in Korean or Japanese.
        
    Returns:
        A text summary of top search results from Naver, Kakao, and Google.
    """
    naver_res = search_naver(query)
    kakao_res = search_kakao(query)
    google_res = search_google(query)
    
    output = []
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

def search_google(query: str) -> str:
    """Query Google Custom Search JSON API. Falls back gracefully if not configured."""
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    cx = os.environ.get("GOOGLE_SEARCH_CX")
    if not api_key or not cx:
        return ""
    
    results = []
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
        results.append(f"[Google Exception] {str(e)}")
        
    return "\n\n".join(results)

