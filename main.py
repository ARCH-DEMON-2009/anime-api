from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from bs4 import BeautifulSoup
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://animedekho.app"
API_KEY = os.getenv("API_KEY", "")
ALLOWED_ORIGINS = [
    "https://animesenpai.in",
    "https://www.animesenpai.in",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app = FastAPI(
    title="Anime API",
    version="2.1.0",
    description="Public API that hides the scraper behind api.animesenpai.in",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    if not API_KEY:
        return
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def fetch_page(url: str):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        print(f"[fetch_page ERROR] {url}: {exc}")
        return None


def parse_article(article):
    title_tag = article.find("h2") or article.find("h3")
    link_tag = article.find("a", href=True)
    image_tag = article.find("img")
    href = link_tag.get("href", "") if link_tag else ""

    item_type = "anime"
    if "movie" in href:
        item_type = "movie"
    elif "cartoon" in href:
        item_type = "cartoon"

    return {
        "title": title_tag.get_text(" ", strip=True) if title_tag else "",
        "slug": href.replace(BASE_URL, "").strip("/"),
        "url": href,
        "image": image_tag.get("src") if image_tag else None,
        "type": item_type,
    }


def extract_servers(html: str):
    soup = BeautifulSoup(html, "html.parser")
    servers = []
    seen = set()

    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src", "").strip()
        if not src or src in seen:
            continue
        if any(skip in src.lower() for skip in ["google.", "facebook.", "twitter.", "doubleclick"]):
            continue
        seen.add(src)
        servers.append({
            "name": f"Server {len(servers) + 1}",
            "type": "embed",
            "embed_url": src,
        })

    for element in soup.find_all(attrs={"data-src": True}):
        src = element.get("data-src", "").strip()
        if not src or src in seen:
            continue
        seen.add(src)
        servers.append({
            "name": f"Server {len(servers) + 1}",
            "type": "embed",
            "embed_url": src,
        })

    return servers


@app.get("/")
def root():
    return {
        "service": "Anime API",
        "status": "ok",
        "public_url": "https://api.animesenpai.in",
        "frontend_url": "https://animesenpai.in",
        "endpoints": ["/search", "/anime/{slug}", "/episode", "/popular", "/recent", "/healthz"],
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/search")
def search_anime(
    q: str = Query(...),
    page: int = Query(1, ge=1, le=10),
    type: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    verify_api_key(x_api_key)

    url = f"{BASE_URL}/?s={q}" if page == 1 else f"{BASE_URL}/page/{page}/?s={q}"
    html = fetch_page(url)
    if not html:
        return JSONResponse({"error": "Failed to fetch search results"}, status_code=500)

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for article in soup.find_all("article"):
        item = parse_article(article)
        if not item["title"] or not item["slug"]:
            continue
        if type and item["type"] != type.lower():
            continue
        results.append(item)

    return {
        "query": q,
        "page": page,
        "count": len(results),
        "results": results[:20],
    }


@app.get("/anime/{slug:path}")
def get_anime_details(slug: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    verify_api_key(x_api_key)

    url = f"{BASE_URL}/{slug}/"
    html = fetch_page(url)
    if not html:
        return JSONResponse({"error": "Anime not found"}, status_code=404)

    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("h1")
    title = title_tag.get_text(" ", strip=True) if title_tag else slug.replace("-", " ").title()

    description_tag = soup.find("div", class_="entry-content")
    description = description_tag.get_text(" ", strip=True)[:600] if description_tag else ""

    image = None
    img = soup.find("img")
    if img:
        image = img.get("src")

    episodes = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if "/epi/" not in href or href in seen:
            continue
        seen.add(href)
        episodes.append({
            "label": anchor.get_text(" ", strip=True) or href,
            "url": href,
        })

    return {
        "title": title,
        "slug": slug,
        "description": description,
        "image": image,
        "episodes": episodes[:100],
    }


@app.get("/episode")
def episode_detail(ep_url: str = Query(...), x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    verify_api_key(x_api_key)

    html = fetch_page(ep_url)
    if not html:
        return JSONResponse({"error": "Episode page not found"}, status_code=404)

    servers = extract_servers(html)
    if not servers:
        return JSONResponse({
            "ep_url": ep_url,
            "server_count": 0,
            "servers": [],
            "recommended": None,
        }, status_code=404)

    recommended = servers[0]
    return {
        "ep_url": ep_url,
        "server_count": len(servers),
        "servers": servers,
        "recommended": recommended,
    }


@app.get("/popular")
def popular(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    verify_api_key(x_api_key)

    html = fetch_page(f"{BASE_URL}/category/anime/")
    if not html:
        return JSONResponse({"error": "Failed to fetch popular anime"}, status_code=500)

    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for article in soup.find_all("article"):
        item = parse_article(article)
        if item["title"]:
            cards.append(item)
    return {"popular": cards[:20]}


@app.get("/recent")
def recent(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    verify_api_key(x_api_key)

    html = fetch_page(f"{BASE_URL}/category/hindi-dub/")
    if not html:
        return JSONResponse({"error": "Failed to fetch recent anime"}, status_code=500)

    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for article in soup.find_all("article"):
        item = parse_article(article)
        if item["title"]:
            cards.append(item)
    return {"recent": cards[:20]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
