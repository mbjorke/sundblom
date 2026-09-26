#!/usr/bin/env python3
"""
Redaktörsomdömet — LLM-baserad urvalsgrind för Tidningen Åland.

Innan en nyhet får bli en Sundblom-ledare bedömer en billig modell
(gemini-2.5-flash, thinking off) om den förtjänar en ledare alls.
Syftet: inte alla artiklar ska översättas — Julius var redaktör som valde
vad som var värt en ledare. Det här kodar det omdömet.

Produktion: körs inuti main.py:s dagliga loop. Loggar varje beslut till
arkiv/selector_logg.json så veckoreflektionen kan mäta urvalsgraden.
"""

import os
import sys
import json
import base64
import logging
import datetime
import requests

log = logging.getLogger(__name__)

# ── Konfiguration ────────────────────────────────────────────────────────────
SELECTOR_MODEL = (
    os.environ.get("SELECTOR_MODEL")
    or os.environ.get("SUNDBLOM_MODEL")
    or "gemini-2.5-flash"
)
SELECTOR_MIN_VALUE = int(os.environ.get("SELECTOR_MIN_VALUE", "6"))
# Vid API-fel: 1 = godkänn ändå (behåll nyhetstäckning), 0 = avvisa (tvinga urval)
SELECTOR_FAIL_OPEN = os.environ.get("SELECTOR_FAIL_OPEN", "1") == "1"

GITHUB_API_BASE = "https://api.github.com"
SELECTOR_LOG_PATH = "arkiv/selector_logg.json"

# ── Redaktörsrubriken ──────────────────────────────────────────────────────────
JUDGE_SYSTEM = """Du är redaktionschefen på Tidningen Åland år 1926. Din uppgift är att
avgöra om en nyhet från Ålands Radio förtjänar en ledarartikel av Julius Sundblom,
eller om den är för obetydlig.

BEDÖM efter tre kriterier:
1. NYHETSVÄRDE — är detta en väsentlig händelse (politik, samhälle, autonomi,
   ekonomi, rättvisa, kultur, sedlighet, internationell/rikspolitisk relevans)
   eller en trivial notis (smålokal notis, ren sportresultat-rapportering,
   väder, rutinåtgärd, personnotis utan större betydelse)?
2. TEMATISK PASSNING — ryms det i Julius redaktionella värld (åländsk
   självstyrelse, svensk identitet, klass- och samhällsordning, sedlighet,
   modernitetens förfall, Sverige/Finland/Ryssland, bildning, egendom)?
   Saknar det all anknytning till något av detta → avvisa.
3. SUBSTANS — finns tillräckligt med innehåll (fakta, agerande, konflikt,
   beslut) att skriva en värdig ledare om, eller är det en tunn notis?

BESLUT: Endast värdiga, tematiskt förankrade nyheter med reell substans ska få
skriv: true. När du tvekar — avvisa. Julius skriver hellre mindre än för mycket.

Svara ENBART med giltig JSON, inga andra tecken, ingen markdown:
{"skriv": true, "nyhetsvarde": 7, "tema": "självstyrelse", "anledning": "Autonomibeslut med rikspolitisk tyngd."}
{"skriv": false, "nyhetsvarde": 2, "tema": "sport", "anledning": "Ren sportresultat-rapportering, ingen redaktionell vinkel."}
"""


# ── Buffert för loggning (töms en gång i slutet av main) ─────────────────────
_buffer: list[dict] = []


def editorial_judgment(headline: str, body: str, source_url: str) -> dict:
    """Returnerar {skriv, nyhetsvarde, tema, anledning, fallback?}."""
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        log.warning("Selector: GOOGLE_API_KEY saknas — fail-open (godkänner).")
        j = {"skriv": True, "nyhetsvarde": None, "tema": "okänt",
             "anledning": "API-nyckel saknas — fail-open", "fallback": True}
        _buffer.append(_entry(headline, source_url, j))
        return j

    user = (f"RUBRIK: {headline}\nKÄLLA: {source_url}\n\n"
            f"ARTIKELINNEHÅLL:\n{(body or '(ingen brödtext)')[:3000]}")

    try:
        from google import genai
        from google.genai import types as genai_types
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=SELECTOR_MODEL,
            contents=user,
            config=genai_types.GenerateContentConfig(
                system_instruction=JUDGE_SYSTEM,
                max_output_tokens=300,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                response_mime_type="application/json",
            ),
        )
        raw = (resp.text or "").strip()
    except Exception as e:
        log.error("Selector: API-fel — %s", e)
        if SELECTOR_FAIL_OPEN:
            j = {"skriv": True, "nyhetsvarde": None, "tema": "fel",
                 "anledning": f"API-fel (fail-open): {e}", "fallback": True}
        else:
            j = {"skriv": False, "nyhetsvarde": 0, "tema": "fel",
                 "anledning": f"API-fel (fail-closed): {e}", "fallback": True}
        _buffer.append(_entry(headline, source_url, j))
        return j

    j = _parse_json(raw)
    if j is None:
        log.error("Selector: kunde inte tolka JSON — %s", raw[:200])
        j = {"skriv": SELECTOR_FAIL_OPEN, "nyhetsvarde": None, "tema": "okänt",
             "anledning": "JSON-tolkningsfel", "fallback": True}
        _buffer.append(_entry(headline, source_url, j))
        return j

    # Backstop: fram tvinga nyhetsvärde-tröskel
    try:
        nv = int(j.get("nyhetsvarde", 0) or 0)
    except (TypeError, ValueError):
        nv = 0
    j["nyhetsvarde"] = nv
    if nv < SELECTOR_MIN_VALUE:
        j["skriv"] = False
        j["anledning"] = (str(j.get("anledning", "")) +
                          f" [nyhetsvärde {nv} < {SELECTOR_MIN_VALUE}]").strip()
    j.setdefault("skriv", False)
    j.setdefault("tema", "?")
    j.setdefault("anledning", "?")
    _buffer.append(_entry(headline, source_url, j))
    return j


def dagens_omdomen() -> list[dict]:
    """Körningens hittills fällda omdömen (ännu ej pushade). Veckokrönikan
    använder dem så att dagens avvisade notiser kommer med i underlaget."""
    return list(_buffer)


def _entry(headline: str, url: str, j: dict) -> dict:
    return {
        "date": datetime.date.today().isoformat(),
        "headline": (headline or "")[:80],
        "url": url,
        "skriv": bool(j.get("skriv")),
        "nyhetsvarde": j.get("nyhetsvarde"),
        "tema": j.get("tema"),
        "anledning": j.get("anledning"),
        "fallback": j.get("fallback", False),
    }


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    # klipp ut JSON om modellen lade till text runt om
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def flush_log() -> None:
    """Pushar buffrade omdömen till arkiv/selector_logg.json (en gång per körning)."""
    if not _buffer:
        return
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    if not token or not repo:
        log.info("Selector: %d omdömen buffrade (ingen GitHub-push lokalt).", len(_buffer))
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{SELECTOR_LOG_PATH}"

    sha = None
    entries: list = []
    resp = requests.get(api_url, headers=headers, params={"ref": branch})
    if resp.status_code == 200:
        sha = resp.json().get("sha")
        entries = json.loads(base64.b64decode(resp.json()["content"]).decode("utf-8"))
    elif resp.status_code != 404:
        log.warning("Selector: kunde inte läsa logg (%s).", resp.status_code)
        return

    entries.extend(_buffer)
    content = json.dumps(entries, ensure_ascii=False, indent=2)
    payload: dict = {
        "message": f"[skip ci] 🧭 Redaktörsomdöme: {len(_buffer)} bedömningar",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    try:
        requests.put(api_url, headers=headers, json=payload).raise_for_status()
        log.info("Selector: %d omdömen loggade till %s.", len(_buffer), SELECTOR_LOG_PATH)
        _buffer.clear()
    except requests.RequestException as e:
        log.warning("Selector: kunde inte pusha logg — %s", e)


# ── Selftest (ingen API-nyckel krävs) ────────────────────────────────────────
if __name__ == "__main__" and "--selftest" in sys.argv:
    print("=== JUDGE_SYSTEM (första raderna) ===")
    print("\n".join(JUDGE_SYSTEM.splitlines()[:6]))
    print("\n=== JSON-tolkning ===")
    for sample in [
        '{"skriv": true, "nyhetsvarde": 7, "tema": "självstyrelse", "anledning": "Autonomibeslut."}',
        'Svar: {"skriv": false, "nyhetsvarde": 2, "tema": "sport", "anledning": "Sportresultat."} klart',
    ]:
        print(f"  in:  {sample[:70]}")
        print(f"  out: {_parse_json(sample)}")
    print(f"\nSELECTOR_MIN_VALUE={SELECTOR_MIN_VALUE} | FAIL_OPEN={SELECTOR_FAIL_OPEN} | MODEL={SELECTOR_MODEL}")
    print("Selftest OK (kräver GOOGLE_API_KEY för riktiga anrop).")
