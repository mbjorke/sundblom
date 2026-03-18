#!/usr/bin/env python3
"""
Sundbloms Radio-kommentarer — Autonom nattlig generator
Hämtar senaste nytt från Ålands Radio och genererar en Sundblomsk ledarartikel.
"""

import os
import sys
import json
import base64
import logging
import datetime
import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
ALANDS_RADIO_URL = "https://alandsradio.ax/nyheter"
GITHUB_API_BASE  = "https://api.github.com"
OUTPUT_HTML      = "index.html"
TEMPLATE_FILE    = os.path.join(os.path.dirname(__file__), "template.html")

# ── GitHub config (from env) ───────────────────────────────────────────────
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")        # "username/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# 1. SCRAPE
# ─────────────────────────────────────────────────────────────────────────────

def fetch_latest_headline() -> tuple[str, str]:
    """
    Returns (headline, url). Falls back to the silence-from-the-mainland
    message if the site is unreachable or empty.
    """
    fallback = (
        "Den öronbedövande tystnaden från fastlandet",
        ALANDS_RADIO_URL,
    )
    try:
        resp = requests.get(ALANDS_RADIO_URL, timeout=15, headers={
            "User-Agent": "SundblomBot/1.0 (+https://github.com)"
        })
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Kunde inte nå Ålands Radio: %s", exc)
        return fallback

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try h2 links inside article cards
    for h2 in soup.select("h2"):
        text = h2.get_text(strip=True)
        if len(text) > 10:
            link_tag = h2.find_parent("a") or h2.find("a")
            href = ""
            if link_tag and link_tag.get("href"):
                href = link_tag["href"]
                if href.startswith("/"):
                    href = "https://alandsradio.ax" + href
            log.info("Hittad rubrik: %s", text)
            return text, href or ALANDS_RADIO_URL

    log.warning("Inga rubriker hittades på sidan.")
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
# 2. AI-LOOP  (Ralph-loop med Sundbloms röst)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Du är Julius Sundblom, grundare av Tidningen Åland och den åländska
autonomirörelsens mest brinnande förkämpe, skrivande år 1920–1928.

Din uppgift är att skriva en ledarartikel / radiokommentar i din autentiska röst.

SPRÅKLIGA KRAV (strikt):
- Ålderdomlig svensk ortografi: hvar, hvarför, hafva, gifva, äfven, blott, ej, icke,
  hvad, hvars, hvarmed, såsom, densamma, deraf, häraf, tillika, allenast, sedermera
- Långa, värdiga meningar med inskjutna bisatser och participfraser
- Inversioner: "Månget öga har sett…", "Aldrig skall det åländska folket…"
- Varierande periodsstruktur: korta hugg följda av långa sveep
- Obligatoriska nyckelfraser (minst två per text):
    "fäderneärvda", "självstyrelsens heliga grundvalar", "låtom oss icke vika",
    "det åländska folkets oförytterliga rätt", "fastlandets godtycke"
- Avsluta alltid med signaturen: *J.S.*

INNEHÅLLSKRAV:
- Koppla nyheten till Ålands konstitutionella ställning, autonomi eller folklig rättvisa
- Visa stridbarhet — det ska BITAS
- Historisk förankring (hänvisa till 1921-beslutet, Nationernas Förbund, demilitariseringen)
- Inled med en dramatisk rubrik (versaler, utan citattecken)

RALPH-LOOP — självgranskning (kör internt, visa ej):
Efter utkastet, fråga dig: Är syntaxen för modern? Saknas patos? Verkar det AI-genererat?
Om JA på någon fråga — skriv om tills texten känns som ett genuint tidningsklipp från 1920-talet.

Svara ENBART med den färdiga texten. Ingen förklaring, ingen inledning."""


def generate_commentary(headline: str, source_url: str) -> str:
    """Anropar Claude API och returnerar den Sundblomska kommentaren."""
    if not ANTHROPIC_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY saknas i miljövariablerna.")

    client = Anthropic(api_key=ANTHROPIC_KEY)

    user_message = (
        f"Dagens nyhet från Ålands Radio:\n\n"
        f"RUBRIK: {headline}\n"
        f"KÄLLA: {source_url}\n\n"
        f"Skriv nu Sundbloms kommentar om denna nyhet."
    )

    log.info("Skickar till Claude API…")
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    commentary = response.content[0].text.strip()
    log.info("Kommentar mottagen (%d tecken).", len(commentary))
    return commentary


# ─────────────────────────────────────────────────────────────────────────────
# 3. FORMAT
# ─────────────────────────────────────────────────────────────────────────────

def render_html(headline: str, commentary: str, source_url: str) -> str:
    """Bäddar in kommentaren i HTML-mallen."""
    today    = datetime.date.today()
    weekdays = ["Måndagen","Tisdagen","Onsdagen","Torsdagen","Fredagen","Lördagen","Söndagen"]
    months   = ["","januari","februari","mars","april","maj","juni",
                "juli","augusti","september","oktober","november","december"]
    date_str = f"{weekdays[today.weekday()]} den {today.day} {months[today.month]} {today.year}"

    # Convert newlines → <p> tags
    paragraphs = "".join(
        f"<p>{p.strip()}</p>"
        for p in commentary.split("\n\n")
        if p.strip()
    )

    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        template = f.read()

    return (template
            .replace("{{DATE}}", date_str)
            .replace("{{HEADLINE}}", headline)
            .replace("{{COMMENTARY}}", paragraphs)
            .replace("{{SOURCE_URL}}", source_url))


# ─────────────────────────────────────────────────────────────────────────────
# 4. PUBLICERA via GitHub API
# ─────────────────────────────────────────────────────────────────────────────

def publish_to_github(html_content: str) -> None:
    """Pushar index.html till GitHub Pages via REST API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise EnvironmentError("GITHUB_TOKEN eller GITHUB_REPO saknas.")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{OUTPUT_HTML}"

    # Hämta befintlig SHA (krävs för uppdatering)
    sha = None
    get_resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
        log.info("Befintlig fil hittad (sha=%s…), uppdaterar.", sha[:7] if sha else "?")
    elif get_resp.status_code == 404:
        log.info("Ingen befintlig fil — skapar ny.")
    else:
        get_resp.raise_for_status()

    payload: dict = {
        "message": f"🗞️ Sundbloms kommentar {datetime.date.today().isoformat()}",
        "content": base64.b64encode(html_content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    put_resp.raise_for_status()
    log.info("✅ Publicerad på GitHub Pages: %s", put_resp.json()["content"]["html_url"])


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("═══ Sundbloms Radio-kommentarer — nattlig körning startar ═══")

    # 1. Scrape
    headline, source_url = fetch_latest_headline()
    log.info("Rubrik: %s", headline)

    # 2. Generera
    commentary = generate_commentary(headline, source_url)

    # 3. Rendera HTML
    html = render_html(headline, commentary, source_url)

    # Spara lokalt också (för debug / artefakt)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML sparad lokalt: %s", OUTPUT_HTML)

    # 4. Publicera
    publish_to_github(html)

    log.info("═══ Klar. ═══")


if __name__ == "__main__":
    main()
