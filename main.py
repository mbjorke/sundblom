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

def fetch_top_headlines(n: int = 2) -> list[tuple[str, str]]:
    """
    Returns a list of (headline, url) for the top-n articles.
    Falls back to a single silence-from-the-mainland entry if unreachable.
    """
    fallback = [(
        "Den öronbedövande tystnaden från fastlandet",
        ALANDS_RADIO_URL,
    )]
    try:
        resp = requests.get(ALANDS_RADIO_URL, timeout=15, headers={
            "User-Agent": "SundblomBot/1.0 (+https://github.com)"
        })
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Kunde inte nå Ålands Radio: %s", exc)
        return fallback

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
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
            results.append((text, href or ALANDS_RADIO_URL))
            if len(results) >= n:
                break

    if not results:
        log.warning("Inga rubriker hittades på sidan.")
        return fallback

    return results


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


def generate_commentary(headlines: list[tuple[str, str]]) -> str:
    """Anropar Claude API och returnerar den Sundblomska kommentaren."""
    if not ANTHROPIC_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY saknas i miljövariablerna.")

    client = Anthropic(api_key=ANTHROPIC_KEY)

    news_block = "\n".join(
        f"RUBRIK {i+1}: {h}\nKÄLLA {i+1}: {u}"
        for i, (h, u) in enumerate(headlines)
    )
    user_message = (
        f"Dagens nyheter från Ålands Radio:\n\n"
        f"{news_block}\n\n"
        f"Skriv nu Sundbloms kommentar. Du kan utgå från en eller båda nyheterna — låt stridsbegäret avgöra."
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

def render_html(headlines: list[tuple[str, str]], commentary: str) -> str:
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

    # Use first headline as the displayed title
    main_headline, _ = headlines[0]

    # Build source links
    source_links = " &nbsp;·&nbsp; ".join(
        f'<a href="{u}" target="_blank" rel="noopener">Nyhet {i+1}</a>'
        for i, (_, u) in enumerate(headlines)
    )

    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        template = f.read()

    return (template
            .replace("{{DATE}}", date_str)
            .replace("{{HEADLINE}}", main_headline)
            .replace("{{COMMENTARY}}", paragraphs)
            .replace("{{SOURCE_URL}}", source_links))


# ─────────────────────────────────────────────────────────────────────────────
# 4. PUBLICERA via GitHub API
# ─────────────────────────────────────────────────────────────────────────────

def _push_file(path: str, html_content: str, commit_message: str) -> str:
    """Pushar en fil till repot via REST API. Returnerar html_url."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise EnvironmentError("GITHUB_TOKEN eller GITHUB_REPO saknas.")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"

    sha = None
    get_resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
        log.info("Befintlig fil hittad (sha=%s…), uppdaterar: %s", sha[:7] if sha else "?", path)
    elif get_resp.status_code == 404:
        log.info("Ingen befintlig fil — skapar ny: %s", path)
    else:
        get_resp.raise_for_status()

    payload: dict = {
        "message": commit_message,
        "content": base64.b64encode(html_content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    put_resp.raise_for_status()
    url = put_resp.json()["content"]["html_url"]
    log.info("✅ Pushad: %s", url)
    return url


def publish_to_github(html_content: str) -> None:
    """Pushar index.html till GitHub Pages."""
    today = datetime.date.today().isoformat()
    _push_file(OUTPUT_HTML, html_content, f"🗞️ Sundbloms kommentar {today}")


def publish_archive_entry(html_content: str) -> None:
    """Sparar dagens artikel i arkiv/YYYY-MM-DD.html."""
    today = datetime.date.today().isoformat()
    _push_file(f"arkiv/{today}.html", html_content, f"📁 Arkiverar {today}")


def rebuild_archive_index() -> None:
    """Hämtar alla arkivfiler och publicerar en uppdaterad arkivindex-sida."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise EnvironmentError("GITHUB_TOKEN eller GITHUB_REPO saknas.")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    list_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/arkiv"
    resp = requests.get(list_url, headers=headers, params={"ref": GITHUB_BRANCH})

    entries = []
    if resp.status_code == 200:
        for item in resp.json():
            name = item.get("name", "")
            if name.endswith(".html") and name != "index.html":
                date_iso = name[:-5]  # strip .html
                entries.append(date_iso)
    elif resp.status_code != 404:
        resp.raise_for_status()

    entries.sort(reverse=True)

    # Formatera datum på svenska
    weekdays = ["Måndag","Tisdag","Onsdag","Torsdag","Fredag","Lördag","Söndag"]
    months   = ["","januari","februari","mars","april","maj","juni",
                "juli","augusti","september","oktober","november","december"]

    def fmt_date(iso: str) -> str:
        try:
            d = datetime.date.fromisoformat(iso)
            return f"{weekdays[d.weekday()]} {d.day} {months[d.month]} {d.year}"
        except ValueError:
            return iso

    rows = "\n".join(
        f'        <li><a href="{e}.html">{fmt_date(e)}</a></li>'
        for e in entries
    )

    html = f"""<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Arkivet — Sundbloms Radio-kommentarer</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IM+Fell+English:ital@0;1&family=UnifrakturMaguntia&family=IM+Fell+English+SC&display=swap" rel="stylesheet">
  <style>
    :root {{ --ink:#1a1207; --paper:#f5ead6; --rule:#5c3d1a; --muted:#6b5540; --accent:#8b1a1a; }}
    *, *::before, *::after {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{
      background:#d9c9a8;
      min-height:100vh; display:flex; justify-content:center; align-items:flex-start;
      padding:2rem 1rem 4rem;
      font-family:'IM Fell English', Georgia, serif; color:var(--ink);
    }}
    .newspaper {{
      background:var(--paper); max-width:780px; width:100%;
      box-shadow:0 2px 4px rgba(0,0,0,.15),0 8px 32px rgba(0,0,0,.25);
      padding:0 0 3rem;
    }}
    .masthead {{
      text-align:center; padding:2.2rem 2.5rem 0;
      border-bottom:4px double var(--rule);
    }}
    .masthead-eyebrow {{ font-family:'IM Fell English SC',serif; font-size:.7rem; letter-spacing:.25em; color:var(--muted); text-transform:uppercase; margin-bottom:.5rem; }}
    .masthead-title {{ font-family:'UnifrakturMaguntia',cursive; font-size:clamp(2.4rem,7vw,3.8rem); line-height:1; margin-bottom:.25rem; }}
    .masthead-subtitle {{ font-family:'IM Fell English SC',serif; font-size:.75rem; letter-spacing:.3em; color:var(--muted); margin-bottom:.8rem; }}
    .masthead-rule {{ height:1px; background:var(--rule); margin:.4rem 0; }}
    .masthead-dateline {{ display:flex; justify-content:space-between; padding:.4rem 0 .6rem; font-size:.7rem; color:var(--muted); font-style:italic; }}
    .content {{ padding:2rem 2.5rem 0; }}
    h2 {{ font-family:'IM Fell English',Georgia,serif; font-size:1.6rem; font-weight:normal; text-align:center; margin-bottom:1.5rem; }}
    ul {{ list-style:none; border-top:1px solid #c8b090; }}
    ul li {{ border-bottom:1px solid #c8b090; }}
    ul li a {{
      display:block; padding:.7rem .2rem;
      font-family:'IM Fell English SC',serif; font-size:.85rem; letter-spacing:.05em;
      color:var(--ink); text-decoration:none;
    }}
    ul li a:hover {{ color:var(--accent); }}
    .back {{ display:block; text-align:center; margin-top:2rem; font-style:italic; font-size:.8rem; color:var(--muted); }}
    .back a {{ color:var(--muted); }}
    @media(max-width:540px) {{ .masthead,.content {{ padding-left:1.2rem; padding-right:1.2rem; }} }}
  </style>
</head>
<body>
<article class="newspaper">
  <header class="masthead">
    <p class="masthead-eyebrow">Grundad anno domini 1891</p>
    <h1 class="masthead-title">Tidningen Åland</h1>
    <p class="masthead-subtitle">Organ för det åländska folkets fria och oförytterliga rätt</p>
    <div class="masthead-rule"></div>
    <div class="masthead-dateline">
      <span>Arkivet</span>
      <span>✦ &nbsp; Sundbloms Radio-kommentarer &nbsp; ✦</span>
      <span>Samtliga utgåvor</span>
    </div>
  </header>
  <div class="content">
    <h2>Tidigare utgåvor</h2>
    <ul>
{rows}
    </ul>
    <a class="back" href="../">← Tillbaka till senaste kommentaren</a>
  </div>
</article>
</body>
</html>"""

    _push_file("arkiv/index.html", html, f"📚 Uppdaterar arkivindex ({len(entries)} utgåvor)")
    log.info("Arkivindex uppdaterat med %d poster.", len(entries))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("═══ Sundbloms Radio-kommentarer — nattlig körning startar ═══")

    # 1. Scrape
    headlines = fetch_top_headlines(n=2)
    for i, (h, u) in enumerate(headlines):
        log.info("Rubrik %d: %s", i + 1, h)

    # 2. Generera
    commentary = generate_commentary(headlines)

    # 3. Rendera HTML
    html = render_html(headlines, commentary)

    # Spara lokalt också (för debug / artefakt)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML sparad lokalt: %s", OUTPUT_HTML)

    # 4. Publicera
    publish_to_github(html)

    # 5. Arkivera
    publish_archive_entry(html)
    rebuild_archive_index()

    log.info("═══ Klar. ═══")


if __name__ == "__main__":
    main()
