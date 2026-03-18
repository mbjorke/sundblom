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

def _article_is_hero(h2) -> bool:
    """
    Hero-artiklar: bild ovanför text → layoutdiv direkt under <article> har flex-col men ej flex-row.
    List-kort: bild åt sidan → layoutdiv har flex-row.
    Vi hittar <article>-föräldern och kollar dess första <div>-barns klasser.
    """
    article = h2.find_parent("article")
    if article is None:
        return False
    layout_div = article.find("div", recursive=False)
    if layout_div is None:
        return False
    classes = " ".join(layout_div.get("class", []))
    return "flex-row" not in classes


def fetch_article_body(url: str) -> str:
    """Hämtar brödtexten från en artikelsida. Returnerar tom sträng vid fel."""
    if not url or url == ALANDS_RADIO_URL:
        return ""
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "SundblomBot/1.0 (+https://github.com)"
        })
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Kunde inte hämta artikel (%s): %s", url, exc)
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Ålands Radio lägger artikeltext i <p>-taggar inuti article/main
    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return ""

    paragraphs = [
        p.get_text(strip=True)
        for p in container.find_all("p")
        if len(p.get_text(strip=True)) > 40
    ]
    body = "\n\n".join(paragraphs[:12])  # max 12 stycken
    log.info("Artikelinnehåll hämtat (%d tecken): %s", len(body), url)
    return body


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

    # Bygg resultatlistan: 1 hero (viktigaste) + 1 list-kort (första följdnyheten)
    # så att DOM-ordningen speglar den visuella prioriteringen.
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
# 2. AI-LOOP
# ─────────────────────────────────────────────────────────────────────────────

SUNDBLOM_PROMPT = """Du är Julius Sundblom, grundare av Tidningen Åland och den åländska
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

FAKTAKRAV (strikt):
- Håll dig till de fakta, händelser och personer som framgår av artikelinnehållet
- Återge citat och uttalanden troget — omformulerade i din stil men ej förvrängda
- Lägg inte till information som saknas i källmaterialet
- Nyhetsvärdet och innehållet förblir troget originalet; endast stil och perspektiv är ditt

INNEHÅLLSKRAV:
- Koppla nyheten till Ålands konstitutionella ställning, autonomi eller folklig rättvisa
- Visa stridbarhet — det ska BITAS
- Historisk förankring (hänvisa till 1921-beslutet, Nationernas Förbund, demilitariseringen)
- Inled med en dramatisk rubrik (versaler, utan citattecken)

RALPH-LOOP — självgranskning (kör internt, visa ej):
Efter utkastet, fråga dig: Är syntaxen för modern? Saknas patos? Verkar det AI-genererat?
Om JA på någon fråga — skriv om tills texten känns som ett genuint tidningsklipp från 1920-talet.

Svara ENBART med den färdiga texten. Ingen förklaring, ingen inledning."""


JOSEFINA_PROMPT = """Du är Josefina Jansson, en av Ålands Radio mest erfarna och respekterade reportrar och programledare. Du har följt åländsk lokalpolitik, samhällsliv och kultur i decennier.

Din uppgift är att skriva ett kort nutida nyhetsreportage / kommentar i din autentiska röst.

FAKTAKRAV (strikt):
- Håll dig till de fakta, händelser och personer som framgår av artikelinnehållet
- Återge citat och uttalanden troget — omformulerade i din stil men ej förvrängda
- Lägg inte till information som saknas i källmaterialet
- Nyhetsvärdet och innehållet förblir troget originalet; endast stil och perspektiv är ditt

STILKRAV:
- Moderna, klara meningar — inga långa omständliga konstruktioner
- Tillgänglig och varm ton, som om du berättar för en lyssnare du känner
- Lokal förankring: Åland, ålänningarna, deras vardag
- Kort intro som hakar in läsaren direkt
- Använd citat och namn som finns i källmaterialet
- Nutida perspektiv — vad händer just nu, vad betyder det för folk här?
- Avsluta med en framåtblickande mening eller öppen fråga

FORMAT:
- 3–4 stycken, cirka 150–200 ord totalt
- Inga rubriker i versaler, inga arkaismer
- Signera inte — byline läggs till separat

JOSEFINA-LOOPEN — självgranskning (kör internt, visa ej):
Efter utkastet, ställ dig dessa frågor:
1. Låter det som radio — naturligt och talat, inte som en tidningsartikel?
2. Känns det lokalt förankrat, eller kunde det handla om vilket samhälle som helst?
3. Är ingressen tillräckligt skarp — skulle en lyssnare stanna kvar?
4. Saknas det mänsklig värme eller ett konkret exempel från verkligheten?
5. Verkar det AI-genererat — för slätt, för korrekt, för opersonligt?
Om JA på någon fråga — skriv om tills texten känns som ett äkta Josefina-inslag från Ålandsnytt.

Svara ENBART med den färdiga texten."""


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


def generate_josefina(headline: str, source_url: str, body: str = "") -> str:
    """Genererar Josefina Janssons nutida reportage om följdnyheten."""
    if not ANTHROPIC_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY saknas i miljövariablerna.")
    log.info("Genererar Josefina-kommentar…")
    news = _build_news_block(headline, source_url, body)
    text = _call_api(
        JOSEFINA_PROMPT,
        f"Nyhet att kommentera:\n\n{news}\n\nSkriv nu ditt reportage om denna nyhet.",
        max_tokens=600,
    )
    log.info("Josefina klar (%d tecken).", len(text))
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


def _julius_block(headline: str, url: str, text: str) -> str:
    return f"""  <div class="content">
    <p class="article-kicker">Radiokommentar · Julius Sundblom</p>
    <h2 class="article-headline">{headline}</h2>
    <p class="article-deck">En betraktelse öfver dagens skeenden i ljuset af Ålands eviga kamp</p>
    <div class="byline-rule"><span class="byline">Julius Sundblom · Tidningen Åland</span></div>
    <div class="article-body">{_to_paragraphs(text)}</div>
    <div class="ornament">— ✦ —</div>
  </div>"""


def _josefina_block(headline: str, url: str, text: str) -> str:
    return f"""  <div class="content">
    <p class="modern-kicker">Radiokommentar · Ålands Radio</p>
    <h2 class="modern-headline">{headline}</h2>
    <div class="modern-byline-row">
      <span class="modern-byline-name">Josefina Jansson</span>
      <span class="modern-byline-org">· Ålands Radio</span>
    </div>
    <div class="modern-body">{_to_paragraphs(text)}</div>
    <p class="modern-source">Källa: <a href="{url}" target="_blank" rel="noopener">Ålands Radio</a></p>
  </div>"""


def render_html(headlines: list[tuple[str, str]],
                sundblom_text: str,
                josefina_text: str,
                julius_first: bool = True) -> str:
    """Bäddar in båda kommentarerna i HTML-mallen."""
    today    = datetime.date.today()
    weekdays = ["Måndagen","Tisdagen","Onsdagen","Torsdagen","Fredagen","Lördagen","Söndagen"]
    months   = ["","januari","februari","mars","april","maj","juni",
                "juli","augusti","september","oktober","november","december"]
    date_str = f"{weekdays[today.weekday()]} den {today.day} {months[today.month]} {today.year}"

    headline_1, url_1 = headlines[0]
    headline_2, url_2 = headlines[1] if len(headlines) > 1 else headlines[0]

    julius  = _julius_block(headline_1, url_1, sundblom_text)
    josefina = _josefina_block(headline_2, url_2, josefina_text)

    if julius_first:
        article_top, article_bottom = julius, josefina
        tm_left_year, tm_left_label, tm_left_class = "MCMXXI", "Då", "tm-era--past"
        tm_right_year, tm_right_label, tm_right_class = "2026", "Nu", "tm-era--now"
    else:
        article_top, article_bottom = josefina, julius
        tm_left_year, tm_left_label, tm_left_class = "2026", "Nu", "tm-era--now"
        tm_right_year, tm_right_label, tm_right_class = "MCMXXI", "Då", "tm-era--past"

    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        template = f.read()

    return (template
            .replace("{{DATE}}", date_str)
            .replace("{{ARTICLE_TOP}}", article_top)
            .replace("{{ARTICLE_BOTTOM}}", article_bottom)
            .replace("{{TM_LEFT_YEAR}}", tm_left_year)
            .replace("{{TM_LEFT_LABEL}}", tm_left_label)
            .replace("{{TM_LEFT_CLASS}}", tm_left_class)
            .replace("{{TM_RIGHT_YEAR}}", tm_right_year)
            .replace("{{TM_RIGHT_LABEL}}", tm_right_label)
            .replace("{{TM_RIGHT_CLASS}}", tm_right_class)
            .replace("{{SOURCE_URL_1}}", url_1))


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

    # 2. Hämta artikelinnehåll
    headline_1, url_1 = headlines[0]
    headline_2, url_2 = headlines[1] if len(headlines) > 1 else headlines[0]
    body_1 = fetch_article_body(url_1)
    body_2 = fetch_article_body(url_2)

    # 3. Generera — två röster
    sundblom_text = generate_sundblom(headline_1, url_1, body_1)
    josefina_text = generate_josefina(headline_2, url_2, body_2)

    # 4. Rendera HTML — Julius först på jämna dagar, Josefina på udda
    julius_first = datetime.date.today().day % 2 == 0
    log.info("Ordning: %s", "Julius → Josefina" if julius_first else "Josefina → Julius")
    html = render_html(headlines, sundblom_text, josefina_text, julius_first)

    # Spara lokalt också (för debug / artefakt)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML sparad lokalt: %s", OUTPUT_HTML)

    # 5. Publicera
    publish_to_github(html)

    # 6. Arkivera
    publish_archive_entry(html)
    rebuild_archive_index()

    log.info("═══ Klar. ═══")


if __name__ == "__main__":
    main()
