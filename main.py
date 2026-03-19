#!/usr/bin/env python3
"""
Åland igår och idag — Autonom nattlig generator
Hämtar senaste nytt från Ålands Radio.
Vänster kolumn: Julius Sundbloms AI-tolkning (1920-tal).
Höger kolumn: Originalartikeln från Ålands Radio.
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

def _article_is_hero(h2) -> bool:
    """
    Hero-artiklar: bild ovanför text → layoutdiv direkt under <article> har flex-col men ej flex-row.
    List-kort: bild åt sidan → layoutdiv har flex-row.
    """
    article = h2.find_parent("article")
    if article is None:
        return False
    layout_div = article.find("div", recursive=False)
    if layout_div is None:
        return False
    classes = " ".join(layout_div.get("class", []))
    return "flex-row" not in classes


def fetch_article_body(url: str) -> tuple[str, str]:
    """
    Hämtar brödtexten och byline från en artikelsida.
    Returnerar (body_text, author_name).
    """
    if not url or url == ALANDS_RADIO_URL:
        return "", ""
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "SundblomBot/1.0 (+https://github.com)"
        })
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Kunde inte hämta artikel (%s): %s", url, exc)
        return "", ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Hitta författare ───────────────────────────────────────────────────
    author = ""

    # 1. rel="author"
    author_tag = soup.find("a", rel="author")
    if author_tag:
        author = author_tag.get_text(strip=True)

    # 2. itemprop="author"
    if not author:
        author_tag = soup.find(itemprop="author")
        if author_tag:
            author = author_tag.get_text(strip=True)

    # 3. Klass som innehåller "author" eller "byline"
    if not author:
        for cls in ["author", "byline", "reporter"]:
            tag = soup.find(class_=lambda c: c and cls in " ".join(c).lower() if c else False)
            if tag:
                text = tag.get_text(strip=True)
                if 3 < len(text) < 60:
                    author = text
                    break

    # 4. <meta name="author">
    if not author:
        meta = soup.find("meta", attrs={"name": "author"})
        if meta and meta.get("content"):
            author = meta["content"].strip()

    if author:
        log.info("Författare hittad: %s", author)
    else:
        log.info("Ingen författare hittad för: %s", url)

    # ── Hämta brödtext ────────────────────────────────────────────────────
    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return "", author

    paragraphs = [
        p.get_text(strip=True)
        for p in container.find_all("p")
        if len(p.get_text(strip=True)) > 40
    ]
    body = "\n\n".join(paragraphs[:12])  # max 12 stycken
    log.info("Artikelinnehåll hämtat (%d tecken): %s", len(body), url)
    return body, author


def fetch_top_headlines(n: int = 2) -> list[tuple[str, str]]:
    """
    Returns a list of (headline, url) for the top-n articles,
    hero-articles (visually prominent) sorted before list-cards.
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
        server_date = resp.headers.get("Date", "saknas")
        log.info("Ålands Radio server-tid (HTTP Date-header): %s", server_date)
    except requests.RequestException as exc:
        log.warning("Kunde inte nå Ålands Radio: %s", exc)
        return fallback

    soup = BeautifulSoup(resp.text, "html.parser")

    heroes, list_cards = [], []
    for h2 in soup.select("h2"):
        text = h2.get_text(strip=True)
        if len(text) <= 10:
            continue
        link_tag = h2.find_parent("a") or h2.find("a")
        href = ""
        if link_tag and link_tag.get("href"):
            href = link_tag["href"]
            if href.startswith("/"):
                href = "https://alandsradio.ax" + href
        entry = (text, href or ALANDS_RADIO_URL)
        if _article_is_hero(h2):
            heroes.append(entry)
        else:
            list_cards.append(entry)

    result_heroes = heroes[:1]
    result_list   = list_cards[:max(0, n - len(result_heroes))]
    results = result_heroes + result_list

    if not results:
        log.warning("Inga rubriker hittades på sidan.")
        return fallback

    for i, (h, _) in enumerate(results):
        kind = "hero" if i < len(result_heroes) else "list"
        log.info("Rubrik %d (%s): %s", i + 1, kind, h)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 2. AI-LOOP (Julius only)
# ─────────────────────────────────────────────────────────────────────────────

SUNDBLOM_PROMPT = """Du är Julius Sundblom (1865–1945), grundare av Tidningen Åland och den åländska
autonomirörelsens mest brinnande förkämpe, skrivande år 1920–1928.

DIN POLITISKA VÄRLDSSYN (låt den genomsyra texten):
- Ålands svenska identitet och självstyre är din absoluta livsuppgift — allt annat är sekundärt
- Du tillhör SFP och den bildade svenska medelklassen; du talar för folket men uppifrån
- Efter finska inbördeskriget 1918 betraktar du socialismen som ett civilisationshot — de röda
  var ett varningstecken om vad som händer när pöbeln tar makten
- Du är djupt skeptisk till arbetarrörelsens krav; ordning, egendom och bildning är samhällets
  grundvalar, inte klasskamp
- Kvinnor är samhällets moraliska ryggrad — i hemmet. Den offentliga sfären tillhör männen.
  Kvinnlig rösträtt (1906) accepterar du formellt men ogärna; en kvinna som tränger sig in i
  politiken har missförstått sin kallelse
- Du är paternalistisk: du vet bättre än gemene man vad som är gott för Åland
- Ryssland/Sovjet är barbari; Finland är ett hot om det inte respekterar autonomin;
  Sverige är civilisationens vagga
- Du är lutheran och tror på att plikten, ordningen och Gud håller samhället samman
- Moderniteten — jazz, lösa seder, storstadsliv — är ett förfall du betraktar med avsmak

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

FAKTAKRAV (strikt):
- Håll dig till de fakta, händelser och personer som framgår av artikelinnehållet
- Återge citat och uttalanden troget — omformulerade i din stil men ej förvrängda
- Lägg inte till information som saknas i källmaterialet
- Nyhetsvärdet och innehållet förblir troget originalet; endast stil och perspektiv är ditt

INNEHÅLLSKRAV:
- Koppla nyheten till din världssyn — autonomi, klassenordning, sedlighet, svensk identitet
- Visa stridbarhet — det ska BITAS
- Historisk förankring (hänvisa till 1921-beslutet, Nationernas Förbund, inbördeskriget 1918)
- Inled med en dramatisk rubrik (versaler, utan citattecken)

ANACHRONISMER ATT UNDVIKA (dessa ord/begrepp existerade ej eller användes ej 1920):
- Skriv aldrig: feminism, jämställdhet, strukturer, identitet (i modern mening),
  stress, media, opinion, integration, dialog, bollplank, feedback, chef (i modern mening),
  eller moderna engelska lånord
- Citera ALDRIG svordomar, skällsord eller vulgära uttryck direkt — det är under
  tidningens värdighet. Referera till dem som "nesliga tillmälen", "skymfliga yttranden
  af det lägsta slag", "ord som ej pryder ett anständigt blad", "en rå muns ofog"

CITATTECKEN-FÖRBUDET — mycket viktigt:
Använd ALDRIG citattecken för att rättfärdiga ett modernt ord eller begrepp.
Om ett ord känns för modernt för 1920-talet — använd det inte alls, ens med citattecken.
Omskriv hela begreppet med genuint 1920-talsspråk.
Exempel på vad som INTE är tillåtet:
  ✗ "bollplank"  ✗ "rent spel"  ✗ "samma förutsättningar"  ✗ "feedback"
Ersätt istället med omskrivningar som: "rådplägningsorgan", "hederlig täflan",
"lika villkor inför ordningen", "tillrättavisning" — eller konstruera meningen
så att det moderna begreppet inte behövs alls.

RALPH-LOOP — självgranskning (kör internt, visa ej):
1. Är syntaxen äkta 1920-tal — inga moderna konstruktioner, inga anachronismer?
2. Har jag av misstag skrivit ut svordomar eller vulgära ord? Om ja — omskriv omgående.
3. Lyser världssynen igenom — klassmedvetandet, misstron mot socialism, synen på könsroller?
4. Verkar det AI-genererat eller som ett genuint tidningsklipp?
Om JA på någon fråga — skriv om tills texten känns äkta.

Svara ENBART med den färdiga texten. Ingen förklaring, ingen inledning."""


def _call_api(system: str, user: str, max_tokens: int = 1200) -> str:
    client = Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def _build_news_block(headline: str, source_url: str, body: str) -> str:
    block = f"RUBRIK: {headline}\nKÄLLA: {source_url}"
    if body:
        block += f"\n\nARTIKELINNEHÅLL:\n{body}"
    return block


def generate_sundblom(headline: str, source_url: str, body: str = "") -> str:
    """Genererar Julius Sundbloms ledarartikel om topnyheten."""
    if not ANTHROPIC_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY saknas i miljövariablerna.")
    log.info("Genererar Sundblom-kommentar…")
    news = _build_news_block(headline, source_url, body)
    text = _call_api(
        SUNDBLOM_PROMPT,
        f"Dagens nyhet från Ålands Radio:\n\n{news}\n\nSkriv nu Sundbloms kommentar om denna nyhet.",
    )
    log.info("Sundblom klar (%d tecken).", len(text))
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 3. FORMAT
# ─────────────────────────────────────────────────────────────────────────────

def _to_paragraphs(text: str) -> str:
    return "".join(
        f"<p>{p.strip()}</p>"
        for p in text.split("\n\n")
        if p.strip()
    )


def render_html(headlines: list[tuple[str, str]],
                julius_texts: list[str],
                originals: list[tuple[str, str]]) -> str:
    """
    Bäddar in alla texter i HTML-mallen.
    originals: lista av (body_text, author_name) per artikel
    """
    today    = datetime.date.today()
    weekdays = ["Måndagen","Tisdagen","Onsdagen","Torsdagen","Fredagen","Lördagen","Söndagen"]
    months   = ["","januari","februari","mars","april","maj","juni",
                "juli","augusti","september","oktober","november","december"]
    date_str = f"{weekdays[today.weekday()]} den {today.day} {months[today.month]} {today.year}"

    headline_1, url_1 = headlines[0]
    headline_2, url_2 = headlines[1] if len(headlines) > 1 else headlines[0]

    body_1, author_1 = originals[0]
    body_2, author_2 = originals[1] if len(originals) > 1 else originals[0]

    # Fallback om ingen text hittades
    if not body_1:
        body_1 = "Artikelinnehåll ej tillgängligt — besök Ålands Radio för att läsa originalet."
    if not body_2:
        body_2 = "Artikelinnehåll ej tillgängligt — besök Ålands Radio för att läsa originalet."

    author_display_1 = author_1 if author_1 else "Ålands Radio"
    author_display_2 = author_2 if author_2 else "Ålands Radio"

    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        template = f.read()

    return (template
            .replace("{{DATE}}", date_str)
            .replace("{{HEADLINE_1}}", headline_1)
            .replace("{{HEADLINE_2}}", headline_2)
            .replace("{{JULIUS_1}}", _to_paragraphs(julius_texts[0]))
            .replace("{{JULIUS_2}}", _to_paragraphs(julius_texts[1]))
            .replace("{{ORIGINAL_1}}", _to_paragraphs(body_1))
            .replace("{{ORIGINAL_2}}", _to_paragraphs(body_2))
            .replace("{{AUTHOR_1}}", author_display_1)
            .replace("{{AUTHOR_2}}", author_display_2)
            .replace("{{SOURCE_URL_1}}", url_1)
            .replace("{{SOURCE_URL_2}}", url_2))


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
    _push_file(OUTPUT_HTML, html_content, f"🗞️ Åland igår och idag {today}")


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
                date_iso = name[:-5]
                entries.append(date_iso)
    elif resp.status_code != 404:
        resp.raise_for_status()

    entries.sort(reverse=True)

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
  <title>Arkivet — Åland igår och idag</title>
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
    <h1 class="masthead-title">Åland igår och idag</h1>
    <p class="masthead-subtitle">Julius Sundblom tolkar · Ålands Radio berättar</p>
    <div class="masthead-rule"></div>
    <div class="masthead-dateline">
      <span>Arkivet</span>
      <span>✦ &nbsp; Samtliga utgåvor &nbsp; ✦</span>
      <span>Åland igår och idag</span>
    </div>
  </header>
  <div class="content">
    <h2>Tidigare utgåvor</h2>
    <ul>
{rows}
    </ul>
    <a class="back" href="../">← Tillbaka till senaste utgåvan</a>
  </div>
</article>
</body>
</html>"""

    _push_file("arkiv/index.html", html, f"📚 Uppdaterar arkivindex ({len(entries)} utgåvor)")
    log.info("Arkivindex uppdaterat med %d poster.", len(entries))


# ─────────────────────────────────────────────────────────────────────────────
# 5. OG-BILD (skärmdump)
# ─────────────────────────────────────────────────────────────────────────────

def generate_og_image() -> bytes | None:
    """Tar en 1200×630 skärmdump av index.html och returnerar PNG-bytes."""
    try:
        import threading
        import http.server
        import time
        from playwright.sync_api import sync_playwright

        class SilentHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

        httpd = http.server.HTTPServer(("", 8765), SilentHandler)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.daemon = True
        thread.start()
        time.sleep(1)

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto("http://localhost:8765/index.html",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)  # Extra tid för typsnitt
            png = page.screenshot(full_page=False)
            browser.close()

        httpd.shutdown()
        log.info("OG-bild genererad (%d bytes).", len(png))
        return png
    except Exception as exc:
        log.warning("Kunde inte generera OG-bild: %s", exc)
        return None


def publish_og_image(png_bytes: bytes) -> None:
    """Pushar og-image.png till repot via GitHub API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.warning("GitHub-miljövariabler saknas — OG-bild ej pushad.")
        return

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/og-image.png"

    sha = None
    get_resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
    elif get_resp.status_code != 404:
        get_resp.raise_for_status()

    payload: dict = {
        "message": "🖼️ Uppdaterar OG-bild",
        "content": base64.b64encode(png_bytes).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    put_resp.raise_for_status()
    log.info("✅ OG-bild pushad.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("═══ Åland igår och idag — nattlig körning startar ═══")

    # 1. Scrape rubriker
    headlines = fetch_top_headlines(n=2)

    headline_1, url_1 = headlines[0]
    headline_2, url_2 = headlines[1] if len(headlines) > 1 else headlines[0]

    # 2. Hämta artikelinnehåll + författare
    body_1, author_1 = fetch_article_body(url_1)
    body_2, author_2 = fetch_article_body(url_2)

    # 3. Generera Julius (2 texter)
    julius_1 = generate_sundblom(headline_1, url_1, body_1)
    julius_2 = generate_sundblom(headline_2, url_2, body_2)

    # 4. Rendera HTML
    html = render_html(
        headlines,
        [julius_1, julius_2],
        [(body_1, author_1), (body_2, author_2)],
    )

    # Spara lokalt (för debug / artefakt)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML sparad lokalt: %s", OUTPUT_HTML)

    # 5. Publicera
    publish_to_github(html)

    # 6. OG-bild (skärmdump av den lokalt sparade index.html)
    og_png = generate_og_image()
    if og_png:
        publish_og_image(og_png)

    # 7. Arkivera
    publish_archive_entry(html)
    rebuild_archive_index()

    log.info("═══ Klar. ═══")


if __name__ == "__main__":
    main()
