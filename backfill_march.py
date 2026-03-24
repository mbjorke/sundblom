"""
backfill_march.py — Fyller i saknade Mars-artiklar från Ålands Radio.

För varje dag i mars 2026 som saknar artikel i src/content/articles/:
  1. Hämtar den första artikeln publicerad den dagen (från sitemap)
  2. Scrapar innehåll
  3. Genererar Julius-text via Claude API
  4. Sparar JSON till repot via GitHub API
"""

import base64
import datetime
import json
import logging
import os
import re
import time
from collections import defaultdict

import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

# ── Konfiguration ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
SUNDBLOM_MODEL = os.environ.get("SUNDBLOM_MODEL", "claude-haiku-4-5-20251001")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_API_BASE = "https://api.github.com"

HEADERS_GH = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
HEADERS_WEB = {"User-Agent": "SundblomBackfill/1.0"}

# ── Hjälpfunktioner ───────────────────────────────────────────────────────────

def slugify(text: str, max_length: int = 60) -> str:
    text = text.lower()
    text = re.sub(r"[åä]", "a", text)
    text = re.sub(r"ö", "o", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_length].rstrip("-")


def push_file(path: str, content: str, message: str) -> None:
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    sha = None
    resp = requests.get(api_url, headers=HEADERS_GH, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 200:
        sha = resp.json()["sha"]
    payload: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(api_url, headers=HEADERS_GH, json=payload)
    r.raise_for_status()


def fetch_article(url: str) -> tuple[str, str, str]:
    """Returnerar (body_text, author, byline) från en Ålands Radio-artikel."""
    try:
        r = requests.get(url, headers=HEADERS_WEB, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Brödtext
        body_parts = []
        for el in soup.select("article p, .article-body p, main p"):
            t = el.get_text(strip=True)
            if t and len(t) > 30:
                body_parts.append(t)
        body = "\n\n".join(body_parts[:12])

        # Författare
        author = ""
        for sel in [".byline", ".author", '[rel="author"]', ".radio-byline"]:
            el = soup.select_one(sel)
            if el:
                author = el.get_text(strip=True)
                break

        return body, author, ""
    except Exception as e:
        log.warning("Kunde inte hämta %s: %s", url, e)
        return "", "", ""


def generate_julius(headline: str, url: str, body: str) -> tuple[str, int, int]:
    """Genererar Julius-text. Returnerar (text, input_tokens, output_tokens)."""
    from main import SUNDBLOM_PROMPT  # återanvänd prompten

    client = Anthropic(api_key=ANTHROPIC_KEY)
    news_block = f"RUBRIK: {headline}\nKÄLLA: {url}"
    if body:
        news_block += f"\n\nARTIKELINNEHÅLL:\n{body}"

    response = client.messages.create(
        model=SUNDBLOM_MODEL,
        max_tokens=500,
        system=SUNDBLOM_PROMPT,
        messages=[{"role": "user", "content": news_block}],
    )
    text = response.content[0].text.strip()
    return text, response.usage.input_tokens, response.usage.output_tokens


def get_existing_dates() -> set[str]:
    """Hämtar datum vi redan har artiklar för via GitHub API."""
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/src/content/articles"
    r = requests.get(api_url, headers=HEADERS_GH, params={"ref": GITHUB_BRANCH})
    r.raise_for_status()
    dates = set()
    for f in r.json():
        name = f["name"]
        if name.endswith(".json") and name.startswith("2026-03"):
            dates.add(name[:10])
    return dates


def get_sitemap_march() -> dict[str, list[tuple[datetime.datetime, str]]]:
    """Hämtar alla mars-artiklar från sitemap, grupperade per dag."""
    r = requests.get("https://alandsradio.ax/sitemap.xml", headers=HEADERS_WEB, timeout=30)
    soup = BeautifulSoup(r.text, "xml")
    by_day: dict[str, list] = defaultdict(list)
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        lastmod = url_tag.find("lastmod")
        if not loc or not lastmod:
            continue
        if "/nyheter/" not in loc.text and "/feature/" not in loc.text:
            continue
        try:
            dt = datetime.datetime.fromisoformat(lastmod.text.replace("Z", "+00:00"))
            if dt.year == 2026 and dt.month == 3:
                day = dt.strftime("%Y-%m-%d")
                by_day[day].append((dt, loc.text))
        except Exception:
            pass
    # Sortera varje dag kronologiskt
    for day in by_day:
        by_day[day].sort()
    return dict(by_day)


def log_tokens(input_t: int, output_t: int) -> None:
    """Uppdaterar arkiv/token-usage.json."""
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/arkiv/token-usage.json"
    resp = requests.get(api_url, headers=HEADERS_GH)
    if resp.status_code == 200:
        data = json.loads(base64.b64decode(resp.json()["content"]).decode())
        sha = resp.json()["sha"]
    else:
        data = {"total_input": 0, "total_output": 0, "calls": []}
        sha = None
    data["total_input"] += input_t
    data["total_output"] += output_t
    data["calls"].append({
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": SUNDBLOM_MODEL,
        "input": input_t,
        "output": output_t,
        "source": "backfill",
    })
    body: dict = {
        "message": f"📊 Backfill tokens: {input_t}+{output_t}",
        "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    requests.put(api_url, headers=HEADERS_GH, json=body)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("═══ Backfill mars 2026 startar ═══")

    existing = get_existing_dates()
    log.info("Befintliga datum: %s", sorted(existing))

    sitemap = get_sitemap_march()
    missing = sorted(d for d in sitemap if d not in existing)
    log.info("Saknade dagar: %s", missing)

    total_in = total_out = 0

    for day in missing:
        articles = sitemap[day]
        # Ta den kronologiskt första artikeln för dagen
        _, url = articles[0]
        log.info("─── %s: %s ───", day, url)

        body, author, _ = fetch_article(url)
        if not body:
            log.warning("Ingen brödtext — hoppar över %s", url)
            continue

        # Extrahera rubrik från URL
        slug_from_url = url.rstrip("/").split("/")[-1]
        headline = slug_from_url.replace("-", " ").capitalize()

        # Försök hämta riktig rubrik från sidan
        try:
            r = requests.get(url, headers=HEADERS_WEB, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            h1 = soup.find("h1")
            if h1:
                headline = h1.get_text(strip=True)
        except Exception:
            pass

        log.info("Rubrik: %s", headline)

        julius, in_t, out_t = generate_julius(headline, url, body)
        total_in += in_t
        total_out += out_t
        log.info("Julius klar (%d tecken). Tokens: %d in + %d out", len(julius), in_t, out_t)

        slug = slugify(headline)
        article = {
            "headline": headline,
            "julius_text": julius,
            "body": body,
            "author": author,
            "source_url": url,
            "date": day,
            "slug": slug,
            "published_at": f"{day}T06:00:00Z",  # approximation för backfill
        }
        path = f"src/content/articles/{day}-{slug}.json"
        push_file(path, json.dumps(article, ensure_ascii=False, indent=2),
                  f"🗞️ Backfill: {headline[:60]}")
        log.info("Sparad: %s", path)

        time.sleep(2)  # var snäll mot API:erna

    log_tokens(total_in, total_out)
    log.info("═══ Klart! %d dagar backfillades. Totalt %d in + %d out tokens ═══",
             len(missing), total_in, total_out)


if __name__ == "__main__":
    main()
