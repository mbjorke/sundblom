#!/usr/bin/env python3
"""
Åland igår och idag — Autonom nattlig generator
Hämtar senaste nytt från Ålands Radio.
Vänster kolumn: Julius Sundbloms AI-tolkning (1920-tal).
Höger kolumn: Originalartikeln från Ålands Radio.

Ny arkitektur (Astro-rebuild):
  - Skriver artikel-data som JSON till src/content/articles/YYYY-MM-DD-slug.json
  - Astro bygger statisk HTML från JSON-filerna vid deployment
  - Cloudflare Pages kör `bun run build` automatiskt
"""

import os
import re
import sys
import json
import base64
import logging
import datetime
import unicodedata
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types as genai_types

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

# ── GitHub config (from env) ───────────────────────────────────────────────
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")        # "username/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GOOGLE_API_KEY  = os.environ.get("GOOGLE_API_KEY", "")
SUNDBLOM_MODEL  = os.environ.get("SUNDBLOM_MODEL") or "gemini-2.5-flash"


# ─────────────────────────────────────────────────────────────────────────────
# 1. SCRAPE
# ─────────────────────────────────────────────────────────────────────────────

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


def fetch_top_headlines(n: int = 20) -> list[tuple[str, str]]:
    """
    Returns a list of (headline, url) for the top-n articles in DOM-order.
    Ålands Radio visar nyast överst — DOM-ordning ger senast publicerade artiklar.
    seen-urls.json filtrerar redan processade, så n kan vara generöst (default 20).
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

    seen_hrefs = set()
    results = []
    for h2 in soup.select("h2"):
        if len(results) >= n:
            break
        text = h2.get_text(strip=True)
        if len(text) <= 10:
            continue
        link_tag = h2.find_parent("a") or h2.find("a")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"]
        if href.startswith("/"):
            href = "https://alandsradio.ax" + href
        if href in seen_hrefs or href == ALANDS_RADIO_URL:
            continue
        seen_hrefs.add(href)
        results.append((text, href))

    if not results:
        log.warning("Inga rubriker hittades på sidan.")
        return fallback

    for i, (h, url) in enumerate(results):
        log.info("Rubrik %d: %s — %s", i + 1, h, url)
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
- LÄNGD: Skriv EXAKT 3–4 korta stycken. Max 250 ord totalt. Originaltexten är din övre gräns — skriv aldrig längre än den.

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


def _call_api(system: str, user: str, max_tokens: int = 2048) -> str:
    client = genai.Client(api_key=GOOGLE_API_KEY)
    response = client.models.generate_content(
        model=SUNDBLOM_MODEL,
        contents=user,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        ),
    )
    usage = response.usage_metadata
    _log_tokens(usage.prompt_token_count, usage.candidates_token_count)
    return response.text.strip()


def _log_tokens(input_tokens: int, output_tokens: int) -> None:
    """Loggar token-användning och sparar löpande summa i arkiv/token-usage.json."""
    log.info(f"Tokens: {input_tokens} in + {output_tokens} out = {input_tokens + output_tokens} totalt")
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/arkiv/token-usage.json"
    resp = requests.get(api_url, headers=headers)
    if resp.status_code == 200:
        data = json.loads(base64.b64decode(resp.json()["content"]).decode())
        sha = resp.json()["sha"]
    else:
        data = {"total_input": 0, "total_output": 0, "calls": []}
        sha = None
    data["total_input"] += input_tokens
    data["total_output"] += output_tokens
    data["calls"].append({
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": SUNDBLOM_MODEL,
        "input": input_tokens,
        "output": output_tokens,
    })
    body: dict = {
        "message": f"📊 Token-logg: {input_tokens}+{output_tokens} tokens [skip cf]",
        "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    requests.put(api_url, headers=headers, json=body)


def _build_news_block(headline: str, source_url: str, body: str) -> str:
    block = f"RUBRIK: {headline}\nKÄLLA: {source_url}"
    if body:
        block += f"\n\nARTIKELINNEHÅLL:\n{body}"
    return block


def generate_sundblom(headline: str, source_url: str, body: str = "") -> str:
    """Genererar Julius Sundbloms ledarartikel om topnyheten."""
    if not GOOGLE_API_KEY:
        raise EnvironmentError("GOOGLE_API_KEY saknas i miljövariablerna.")
    log.info("Genererar Sundblom-kommentar…")
    news = _build_news_block(headline, source_url, body)
    text = _call_api(
        SUNDBLOM_PROMPT,
        f"Dagens nyhet från Ålands Radio:\n\n{news}\n\nSkriv nu Sundbloms kommentar om denna nyhet.",
    )
    log.info("Sundblom klar (%d tecken).", len(text))
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 3. SLUG
# ─────────────────────────────────────────────────────────────────────────────

def slugify(text: str, max_length: int = 60) -> str:
    """Konverterar en rubrik till en URL-vänlig slug."""
    replacements = {'å':'a','ä':'a','ö':'o','Å':'a','Ä':'a','Ö':'o',
                    'é':'e','è':'e','ê':'e','ü':'u','ä':'a'}
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    # Normalisera resterande unicode
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text[:max_length].rstrip('-')


# ─────────────────────────────────────────────────────────────────────────────
# 4. PUBLICERA via GitHub API
# ─────────────────────────────────────────────────────────────────────────────

def _file_sha(folder: str, slug: str) -> bool:
    """Returnerar True om det redan finns en artikel-JSON med given slug (oavsett datum)."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{folder}"
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200:
        return False
    return any(
        f["name"].endswith(f"-{slug}.json") or f["name"] == f"{slug}.json"
        for f in resp.json()
    )


def _push_file(path: str, content: str, commit_message: str) -> str:
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
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    put_resp.raise_for_status()
    url = put_resp.json()["content"]["html_url"]
    log.info("✅ Pushad: %s", url)
    return url


def load_seen_urls() -> set:
    """Hämtar redan processade artikel-URLar från arkiv/seen-urls.json."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return set()
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/arkiv/seen-urls.json"
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 404:
        return set()
    resp.raise_for_status()
    data = resp.json()
    raw = base64.b64decode(data["content"]).decode("utf-8")
    entries = json.loads(raw)
    urls = {e["url"] for e in entries if "url" in e}
    log.info("Laddade %d redan processade URLar.", len(urls))
    return urls


def save_seen_url(url: str, headline: str, date_iso: str) -> None:
    """Lägger till en URL i arkiv/seen-urls.json."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/arkiv/seen-urls.json"

    # Läs befintlig fil
    sha = None
    entries = []
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 200:
        sha = resp.json().get("sha")
        raw = base64.b64decode(resp.json()["content"]).decode("utf-8")
        entries = json.loads(raw)
    elif resp.status_code != 404:
        resp.raise_for_status()

    entries.append({"url": url, "headline": headline[:80], "date": date_iso})
    new_content = json.dumps(entries, ensure_ascii=False, indent=2)

    payload: dict = {
        "message": f"🔖 Markerar som processad: {headline[:50]} [skip cf]",
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    requests.put(api_url, headers=headers, json=payload).raise_for_status()
    log.info("URL sparad i seen-urls.json: %s", url)


def load_last_headline() -> str:
    """Hämtar senast publicerad rubrik från last_headline.txt i repot."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return ""
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/last_headline.txt"
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    return base64.b64decode(resp.json()["content"]).decode("utf-8").strip()


def save_last_headline(headline: str) -> None:
    """Sparar senast publicerad rubrik till last_headline.txt i repot."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/last_headline.txt"
    sha = None
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 200:
        sha = resp.json().get("sha")
    elif resp.status_code != 404:
        resp.raise_for_status()
    payload: dict = {
        "message": f"🔖 Uppdaterar senaste rubrik: {headline[:60]}",
        "content": base64.b64encode(headline.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    requests.put(api_url, headers=headers, json=payload).raise_for_status()
    log.info("last_headline.txt uppdaterad.")
    _update_build_meta(headers)


def _update_build_meta(headers: dict) -> None:
    """Uppdaterar src/build-meta.json med aktuell tidsstämpel.
    Importeras av index.astro så att CF Pages alltid räknar hemsidan som förändrad."""
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/src/build-meta.json"
    sha = None
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 200:
        sha = resp.json().get("sha")
    meta = {"last_updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    payload: dict = {
        "message": f"🕐 Uppdaterar build-meta [skip cf]",
        "content": base64.b64encode(json.dumps(meta, indent=2).encode()).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    requests.put(api_url, headers=headers, json=payload)
    log.info("build-meta.json uppdaterad.")


def save_article_json(headline: str, julius_text: str, body: str, author: str,
                      source_url: str, date_iso: str, slug: str) -> None:
    """
    Pushar artikel-data som JSON till src/content/articles/YYYY-MM-DD-slug.json.
    Astro läser dessa filer och bygger statisk HTML vid deployment.
    """
    path = f"src/content/articles/{date_iso}-{slug}.json"
    article = {
        "headline": headline,
        "julius_text": julius_text,
        "body": body,
        "author": author or "Ålands Radio",
        "source_url": source_url,
        "date": date_iso,
        "published_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slug": slug,
    }
    content = json.dumps(article, ensure_ascii=False, indent=2)
    _push_file(path, content, f"🗞️ Ny artikel: {headline[:60]} [skip cf]")
    log.info("Artikel JSON sparad: %s", path)


def publish_og_image(png_bytes: bytes) -> None:
    """Pushar og-image.png till public/ via GitHub API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.warning("GitHub-miljövariabler saknas — OG-bild ej pushad.")
        return

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/public/og-image.png"

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
    log.info("═══ Åland igår och idag — daglig körning startar ═══")

    # 1. Scrape upp till 20 rubriker i DOM-ordning
    headlines = fetch_top_headlines(n=20)
    if not headlines:
        log.info("Inga rubriker hittades — avslutar.")
        return

    # 2. Early exit: om topprubrik är oförändrad sedan senaste körning → spara API-anrop
    last_headline = load_last_headline()
    top_headline = headlines[0][0]
    if top_headline == last_headline:
        log.info("Topprubrik oförändrad (%s) — ingen ny artikel att publicera.", top_headline[:60])
        return

    # 3. Ladda redan processade URLar (undviker dubbletter vid helger/högtider)
    seen_urls = load_seen_urls()

    today = datetime.date.today().isoformat()
    new_articles = 0

    for headline, url in headlines:
        if url == ALANDS_RADIO_URL:
            log.info("Fallback-URL — hoppar över: %s", url)
            continue
        if url in seen_urls:
            log.info("Redan processad — hoppar över: %s", url)
            continue

        # Slug-kontroll: om en JSON med samma slug redan finns (oavsett datum) → hoppa över
        slug = slugify(headline)
        existing = _file_sha(f"src/content/articles", slug)
        if existing:
            log.info("Slug redan publicerad — hoppar över: %s", slug)
            save_seen_url(url, headline, today)
            continue

        log.info("─── Processar: %s ───", headline)

        # 3. Hämta artikelinnehåll + författare
        body, author = fetch_article_body(url)

        # 4. Generera Julius
        julius = generate_sundblom(headline, url, body)

        # 6. Spara JSON till src/content/articles/ via GitHub API
        save_article_json(headline, julius, body, author, url, today, slug)

        # 7. Markera som processad
        save_seen_url(url, headline, today)
        seen_urls.add(url)
        new_articles += 1

    if new_articles == 0:
        log.info("Inga nya artiklar att publicera idag.")
    else:
        log.info("%d ny/nya artikel(ar) publicerade.", new_articles)
        save_last_headline(top_headline)

    log.info("═══ Klar. ═══")


if __name__ == "__main__":
    main()
