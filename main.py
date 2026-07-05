from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import APIRouter
import requests
from bs4 import BeautifulSoup
import re
import os
from typing import Optional

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AnimeDekho API",
    description="Unofficial scraper API for animedekho.app — anime listings, details, and episode streams.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# On Render.com set API_PREFIX="" to serve at root (/search, /popular …).
# On Replit keep the default "/anime-api" so the proxy can route correctly.
API_PREFIX = os.environ.get("API_PREFIX", "/anime-api")
router = APIRouter(prefix=API_PREFIX)

BASE_URL = "https://animedekho.app"

# Accept-Encoding intentionally omitted — requests will negotiate only
# gzip/deflate (which it can decode natively).  Including "br" causes the
# server to send Brotli-compressed data that requests cannot decompress
# without the optional brotli package.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[fetch_page ERROR] {url} → {type(e).__name__}: {e}")
        return None


def parse_article(item) -> dict:
    """Extract rich metadata from a search/listing <article> element."""
    title_el  = item.find("h2") or item.find("h3")
    link_el   = item.find("a", href=True)
    img_el    = item.find("img")
    year_el   = item.find("span", class_="year")
    qual_el   = item.find("span", class_="quality")
    dur_el    = item.find("span", class_="duration")
    ep_el     = item.find("span", class_="season-episode")
    rating_el = item.find("span", class_=lambda c: c and "rating" in c and "fa-star" in c)

    href = link_el["href"] if link_el else ""
    item_type = (
        "movie"   if "movie" in href else
        "cartoon" if "cartoon" in href else
        "anime"
    )
    genres = [
        a.get_text(strip=True)
        for a in item.find_all("a", href=True)
        if "category" in a.get("href", "")
    ]

    return {
        "title":          title_el.get_text(strip=True) if title_el else None,
        "slug":           href.replace(BASE_URL, "").strip("/"),
        "url":            href,
        "image":          img_el.get("src") if img_el else None,
        "year":           year_el.get_text(strip=True) if year_el else None,
        "quality":        qual_el.get_text(strip=True) if qual_el else None,
        "duration":       dur_el.get_text(strip=True) if dur_el else None,
        "rating":         rating_el.get_text(strip=True) if rating_el else None,
        "latest_episode": ep_el.get_text(strip=True) if ep_el else None,
        "genres":         genres,
        "type":           item_type,
    }


def detect_server_type(src: str) -> str:
    src_lower = src.lower()
    if "youtube.com" in src_lower or "youtu.be" in src_lower:
        return "youtube"
    if "vimeo.com" in src_lower:
        return "vimeo"
    if "streamtape" in src_lower:
        return "streamtape"
    if "doodstream" in src_lower or "dood." in src_lower:
        return "doodstream"
    if "filemoon" in src_lower:
        return "filemoon"
    if "streamwish" in src_lower:
        return "streamwish"
    if "ok.ru" in src_lower:
        return "ok.ru"
    return "embed"


def extract_servers(html: str) -> list:
    """Return all video server iframes found on an episode page."""
    soup = BeautifulSoup(html, "html.parser")
    servers = []
    seen: set = set()

    # Static iframes
    for i, iframe in enumerate(soup.find_all("iframe", src=True)):
        src = iframe["src"].strip()
        if not src or src in seen:
            continue
        if any(skip in src for skip in ("google.", "facebook.", "twitter.", "doubleclick")):
            continue
        seen.add(src)
        servers.append({
            "name":      f"Server {len(servers) + 1}",
            "type":      detect_server_type(src),
            "embed_url": src,
        })

    # Lazy-loaded iframes (data-src)
    for el in soup.find_all(attrs={"data-src": True}):
        src = el.get("data-src", "").strip()
        if not src or src in seen:
            continue
        if any(k in src for k in ("player", "embed", "stream", "watch", "video")):
            seen.add(src)
            servers.append({
                "name":      f"Server {len(servers) + 1}",
                "type":      detect_server_type(src),
                "embed_url": src,
            })

    return servers


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/")
def root():
    return {
        "message": "AnimeDekho API v2.0 — running!",
        "prefix":  API_PREFIX or "(root)",
        "endpoints": {
            "GET /search?q=&page=&type=": "Search anime (page 1-10, type=anime|movie|cartoon)",
            "GET /anime/{slug}":          "Anime details + full episode list",
            "GET /episode?ep_url=":       "All video servers for an episode URL",
            "GET /popular":               "Recently listed anime (category/anime)",
            "GET /recent":                "Recent Hindi-Dub releases",
        },
    }


@router.get("/search")
def search_anime(
    q:    str           = Query(..., description="Anime name to search"),
    page: int           = Query(1, ge=1, le=10, description="Page number (each page has ~15 results)"),
    type: Optional[str] = Query(None, description="Filter by type: anime | movie | cartoon"),
):
    # animedekho uses ?s= for search and /page/N/ for pagination
    url = f"{BASE_URL}/?s={q}" if page == 1 else f"{BASE_URL}/page/{page}/?s={q}"
    html = fetch_page(url)
    if not html:
        return JSONResponse({"error": "Failed to fetch search results"}, status_code=500)

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.find_all("article"):
        parsed = parse_article(item)
        if not parsed["title"] or not parsed["url"]:
            continue
        if type and parsed["type"] != type.lower():
            continue
        results.append(parsed)

    return {
        "query":   q,
        "page":    page,
        "count":   len(results),
        "results": results,
    }


@router.get("/anime/{slug:path}")
def get_anime_details(slug: str):
    url = f"{BASE_URL}/{slug}/"
    html = fetch_page(url)
    if not html:
        return JSONResponse({"error": "Anime not found"}, status_code=404)

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_el = soup.find("h1")
    title = (title_el.get_text(strip=True) if title_el
             else slug.split("/")[-1].replace("-", " ").title())

    # Description
    desc_el     = soup.find("div", class_="entry-content")
    description = desc_el.get_text(strip=True)[:600] if desc_el else None

    # Cover image
    img_el = (soup.find("img", attrs={"post-id": True}) or
              soup.find("img", class_="wp-post-image"))
    image = img_el.get("src") if img_el else None

    # Rich metadata from .bd sidebar block
    rating = views = duration = latest_ep_info = None
    genres: list = []
    bd = soup.find(class_="bd") or soup.find(class_="entry-meta")
    if bd:
        r_el   = bd.find("span", class_=lambda c: c and "rating" in c)
        d_el   = bd.find("span", class_="duration")
        ep_el  = bd.find("span", class_="season-episode")
        rating        = r_el.get_text(strip=True)  if r_el  else None
        duration      = d_el.get_text(strip=True)  if d_el  else None
        latest_ep_info= ep_el.get_text(strip=True) if ep_el else None

        # Genre list from <li class="rw sm"> that starts with "Genres"
        for li in bd.find_all("li"):
            text = li.get_text(separator=",", strip=True)
            if text.startswith("Genres"):
                genres = [g.strip() for g in text.replace("Genres", "").split(",") if g.strip()]
                break

        views_m = re.search(r"([\d,]+)\s*Views", bd.get_text())
        views   = views_m.group(1) if views_m else None

    # Episode list — links in the form /epi/{anime-slug}-{S}x{E}/
    episodes: list = []
    seen: set      = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/epi/" not in href or href in seen:
            continue
        seen.add(href)
        m = re.search(r"-(\d+)x(\d+)/?$", href)
        if m:
            label = a.get_text(strip=True) or f"S{m.group(1)}-E{m.group(2)}"
            episodes.append({
                "season":  int(m.group(1)),
                "episode": int(m.group(2)),
                "label":   label,
                "url":     href,
            })

    episodes.sort(key=lambda x: (x["season"], x["episode"]))

    return {
        "title":          title,
        "slug":           slug,
        "description":    description,
        "image":          image,
        "rating":         rating,
        "duration":       duration,
        "views":          views,
        "genres":         genres,
        "latest_episode": latest_ep_info,
        "total_episodes": len(episodes),
        "episodes":       episodes[:100],
    }


@router.get("/episode")
def get_episode_servers(
    ep_url: str = Query(..., description="Episode URL (e.g. https://animedekho.app/epi/naruto-1x1/)"),
):
    html = fetch_page(ep_url)
    if not html:
        return JSONResponse({"error": "Episode page not found"}, status_code=404)

    servers = extract_servers(html)
    if not servers:
        return JSONResponse({
            "ep_url": ep_url,
            "error":  "No video servers found on this episode page",
            "servers": [],
        }, status_code=404)

    # Prefer non-YouTube embeds for in-app playback; YouTube as fallback
    recommended = next((s for s in servers if s["type"] != "youtube"), servers[0])

    return {
        "ep_url":       ep_url,
        "server_count": len(servers),
        "servers":      servers,
        "recommended":  recommended,
    }


@router.get("/popular")
def get_popular():
    html = fetch_page(f"{BASE_URL}/category/anime/")
    if not html:
        return JSONResponse({"error": "Failed to fetch popular anime"}, status_code=500)
    soup    = BeautifulSoup(html, "html.parser")
    results = [parse_article(a) for a in soup.find_all("article") if a.find("a", href=True)]
    return {"popular": [r for r in results if r["title"]][:20]}


@router.get("/recent")
def get_recent():
    html = fetch_page(f"{BASE_URL}/category/hindi-dub/")
    if not html:
        return JSONResponse({"error": "Failed to fetch recent episodes"}, status_code=500)
    soup    = BeautifulSoup(html, "html.parser")
    results = [parse_article(a) for a in soup.find_all("article") if a.find("a", href=True)]
    return {"recent": [r for r in results if r["title"]][:20]}


# ── Mount & run ────────────────────────────────────────────────────────────────
app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"AnimeDekho API v2.0 starting on port {port}  prefix='{API_PREFIX}'")
    uvicorn.run(app, host="0.0.0.0", port=port)
