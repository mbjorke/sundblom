#!/usr/bin/env python3
"""
Veckokrönikan — Julius skriver när veckan inte gav någon nyhet värd en ledare.

Bakgrund: redaktörsomdömet (selector.py) avvisar med flit det mesta — sport,
lokalnotiser, personnotiser. På helger och stilla dagar avvisas allt, och då
publicerades tidigare ingenting alls. Över hälften av dagarna sedan mars var
sådana tomma dagar.

I stället för att sänka ribban för ledaren skriver Julius då en krönika: en
betraktelse över veckan som gått, byggd på det som faktiskt stod i Ålands
Radio — både de nyheter som fick en ledare och de notiser som inte gjorde det.

Produktion: anropas från main.py när dagens loop inte gav någon artikel.
Läser materialet från arbetskatalogen (repot är utcheckat i GitHub Actions),
så ingen extra API-trafik krävs för att samla underlaget.
"""

import os
import re
import sys
import json
import glob
import logging
import datetime

log = logging.getLogger(__name__)

# ── Konfiguration ────────────────────────────────────────────────────────────
KRONIKA_ENABLED = os.environ.get("KRONIKA_ENABLED", "1") == "1"
# Krönikan skrivs bara i dagens sista körning (22:00 EET = 20:00 UTC): dagen
# ska få hela sin chans att bjuda på en riktig nyhet först, och söndagens
# veckobetraktelse (reflect.py, 17:00 UTC) ska hinna före krönikan.
KRONIKA_AFTER_UTC_HOUR = int(os.environ.get("KRONIKA_AFTER_UTC_HOUR", "18"))
# Minsta antal dagar mellan två krönikor (en helg ger alltså en, ej två).
KRONIKA_MIN_DAYS = int(os.environ.get("KRONIKA_MIN_DAYS", "3"))
# Så många rubriker måste veckan ha bjudit på för att en krönika ska bära.
KRONIKA_MIN_RUBRIKER = int(os.environ.get("KRONIKA_MIN_RUBRIKER", "5"))
# Hur många dagar bakåt krönikan blickar.
KRONIKA_DAGAR = int(os.environ.get("KRONIKA_DAGAR", "7"))

_ROOT = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(_ROOT, "src", "content", "articles")
SELECTOR_LOG = os.path.join(_ROOT, "arkiv", "selector_logg.json")

KRONIKA_SLUG_PREFIX = "kronika"
MAX_KALLOR = 14          # rubriker i högerkolumnen
MAX_LEDARE_I_PROMPT = 7
MAX_NOTISER_I_PROMPT = 15
BODY_UTDRAG = 400        # tecken ur originaltexten per ledare


# ── Formkrav som läggs ovanpå Julius vanliga röst ────────────────────────────
KRONIKA_FORM = """

FORMEN DENNA GÅNG ÄR EN VECKOKRÖNIKA — INTE EN LEDARE OM EN ENSKILD NYHET.
Detta ersätter längd- och formkraven ovan; världssyn, språk och faktakrav
gäller oförändrat.

- Du blickar tillbaka på veckan som gått i Ålands Radio och binder samman
  flera av dess händelser till en enda betraktelse.
- Väv ihop det stora och det ringa: låt en obetydlig notis bli bild för något
  större om tiden, seden och samhällsordningen.
- Nämn ALDRIG en händelse, person eller uppgift som inte står i underlaget
  nedan. Av notiserna känner du bara rubriken — bygg inte ut dem med
  påhittade detaljer, utan tala om dem i allmänna ordalag.
- Skriv rubriken på FÖRSTA raden, ensam, följd av en tom rad. Rubriken sätts
  med stor begynnelsebokstav och små bokstäver i övrigt — INTE versaler — och
  ska ange att det rör veckan som gått.
- Därefter 4–5 stycken, högst 350 ord tillsammans.
- Avsluta med signaturen *J.S.*
"""


# ─────────────────────────────────────────────────────────────────────────────
# UNDERLAG
# ─────────────────────────────────────────────────────────────────────────────

def _datum_bakat(today: str, dagar: int) -> str:
    d = datetime.date.fromisoformat(today) - datetime.timedelta(days=dagar)
    return d.isoformat()


def _las_artiklar() -> list[dict]:
    """Läser alla artikel-JSON från arbetskatalogen (tyst vid trasig fil)."""
    artiklar = []
    for path in glob.glob(os.path.join(ARTICLES_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                artiklar.append(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Krönika: kunde ej läsa %s — %s", os.path.basename(path), e)
    return artiklar


def _las_selektorlogg() -> list[dict]:
    if not os.path.exists(SELECTOR_LOG):
        return []
    try:
        with open(SELECTOR_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Krönika: kunde ej läsa selector_logg.json — %s", e)
        return []


def samla_underlag(today: str, dagar: int = KRONIKA_DAGAR,
                   extra_omdomen: list[dict] | None = None) -> dict:
    """Veckans material: publicerade ledare + de notiser som avvisades.

    `extra_omdomen` är körningens ännu opushade omdömen från selector.py —
    utan dem saknar krönikan just dagens avvisade notiser.
    """
    grans = _datum_bakat(today, dagar)

    ledare = [
        a for a in _las_artiklar()
        if grans <= a.get("date", "") <= today
        and a.get("kind") != KRONIKA_SLUG_PREFIX
    ]
    ledare.sort(key=lambda a: a.get("date", ""), reverse=True)

    kanda = {a.get("source_url") for a in ledare}
    notiser = []
    for e in _las_selektorlogg() + list(extra_omdomen or []):
        if not (grans <= e.get("date", "") <= today):
            continue
        if e.get("skriv") or e.get("url") in kanda:
            continue
        kanda.add(e.get("url"))
        notiser.append(e)
    notiser.sort(key=lambda e: e.get("date", ""), reverse=True)

    return {"ledare": ledare, "notiser": notiser, "fran": grans, "till": today}


def kallor(underlag: dict, max_antal: int = MAX_KALLOR) -> list[dict]:
    """Rubrikerna som visas i högerkolumnen — nyast först."""
    poster = [
        {"headline": a.get("headline", ""), "url": a.get("source_url", ""),
         "date": a.get("date", "")}
        for a in underlag["ledare"]
    ] + [
        {"headline": e.get("headline", ""), "url": e.get("url", ""),
         "date": e.get("date", "")}
        for e in underlag["notiser"]
    ]
    poster = [p for p in poster if p["headline"] and p["url"]]
    poster.sort(key=lambda p: p["date"], reverse=True)
    return poster[:max_antal]


def antal_rubriker(underlag: dict) -> int:
    return len(underlag["ledare"]) + len(underlag["notiser"])


# ─────────────────────────────────────────────────────────────────────────────
# BESLUT
# ─────────────────────────────────────────────────────────────────────────────

def _publicerat_idag(today: str) -> bool:
    return bool(glob.glob(os.path.join(ARTICLES_DIR, f"{today}-*.json")))


def senaste_kronika() -> str | None:
    """Datum för den senast publicerade krönikan, eller None."""
    datum = [
        os.path.basename(p)[:10]
        for p in glob.glob(os.path.join(ARTICLES_DIR, f"*-{KRONIKA_SLUG_PREFIX}-*.json"))
    ]
    giltiga = [d for d in datum if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    return max(giltiga) if giltiga else None


def bor_skriva_kronika(today: str, now_utc: datetime.datetime,
                       underlag: dict | None = None) -> tuple[bool, str]:
    """Returnerar (beslut, motivering) — motiveringen loggas."""
    if not KRONIKA_ENABLED:
        return False, "krönikan är avstängd (KRONIKA_ENABLED=0)"
    if now_utc.hour < KRONIKA_AFTER_UTC_HOUR:
        return False, (f"för tidigt på dagen ({now_utc.hour:02d} UTC < "
                       f"{KRONIKA_AFTER_UTC_HOUR:02d}) — dagen kan ännu ge en nyhet")
    if _publicerat_idag(today):
        return False, "en utgåva är redan publicerad idag"

    senaste = senaste_kronika()
    if senaste:
        dagar = (datetime.date.fromisoformat(today)
                 - datetime.date.fromisoformat(senaste)).days
        if dagar < KRONIKA_MIN_DAYS:
            return False, (f"krönika skrevs för {dagar} dag(ar) sedan "
                           f"({senaste}), minst {KRONIKA_MIN_DAYS} dagar emellan")

    underlag = underlag if underlag is not None else samla_underlag(today)
    n = antal_rubriker(underlag)
    if n < KRONIKA_MIN_RUBRIKER:
        return False, f"för tunt underlag ({n} rubriker < {KRONIKA_MIN_RUBRIKER})"

    return True, f"ingen utgåva idag och {n} rubriker att blicka tillbaka på"


# ─────────────────────────────────────────────────────────────────────────────
# GENERERING
# ─────────────────────────────────────────────────────────────────────────────

def bygg_prompt(underlag: dict) -> str:
    rader = [
        f"VECKANS MATERIAL UR ÅLANDS RADIO "
        f"({underlag['fran']} till och med {underlag['till']}):",
        "",
        "NYHETER SOM FICK EN LEDARE (du känner innehållet):",
    ]
    ledare = underlag["ledare"][:MAX_LEDARE_I_PROMPT]
    if ledare:
        for a in ledare:
            rader.append(f"- [{a.get('date','')}] {a.get('headline','')}")
            utdrag = (a.get("body") or "").strip().replace("\n", " ")
            if utdrag:
                rader.append(f"    ur originalet: {utdrag[:BODY_UTDRAG]}")
    else:
        rader.append("- (inga — veckan gav ingen nyhet värd en ledare)")

    rader += ["", "VECKANS ÖVRIGA NOTISER (du känner bara rubriken):"]
    notiser = underlag["notiser"][:MAX_NOTISER_I_PROMPT]
    if notiser:
        for e in notiser:
            tema = e.get("tema") or "?"
            rader.append(f"- [{e.get('date','')}] {e.get('headline','')} (ämne: {tema})")
    else:
        rader.append("- (inga)")

    rader += ["", "Skriv nu veckokrönikan."]
    return "\n".join(rader)


def dela_rubrik(text: str, today: str) -> tuple[str, str]:
    """Skiljer rubrikraden från brödtexten."""
    rader = [r for r in (text or "").strip().split("\n")]
    while rader and not rader[0].strip():
        rader.pop(0)
    if not rader:
        return f"Veckokrönika {today}", ""
    rubrik = rader[0].strip().strip("#").strip().strip('"“”').strip()
    brod = "\n".join(rader[1:]).strip()
    # Orimlig rubrikrad (modellen skrev rakt in i brödtexten) → behåll allt
    if not brod or len(rubrik) > 120:
        return f"Veckokrönika {today}", (text or "").strip()
    if rubrik.isupper():
        rubrik = rubrik.capitalize()
    return rubrik, brod


def generera(underlag: dict, base_prompt: str, riktlinjer: str, call_api) -> tuple[str, str]:
    """Genererar krönikan. `call_api(system, user, max_tokens) -> str` kommer
    från main.py, så tokenloggning och modellval förblir på ett ställe."""
    system = base_prompt + KRONIKA_FORM
    user = f"{riktlinjer}{bygg_prompt(underlag)}"
    text = call_api(system, user, 2048)
    return dela_rubrik(text, underlag["till"])


# ── Selftest (ingen API-nyckel krävs) ────────────────────────────────────────
if __name__ == "__main__" and "--selftest" in sys.argv:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    idag = sys.argv[sys.argv.index("--datum") + 1] if "--datum" in sys.argv \
        else datetime.date.today().isoformat()
    nu = datetime.datetime(2000, 1, 1, 20, 0)  # sen körning

    u = samla_underlag(idag)
    print(f"=== Underlag {u['fran']} → {u['till']} ===")
    print(f"  ledare: {len(u['ledare'])}, notiser: {len(u['notiser'])}")
    beslut, varfor = bor_skriva_kronika(idag, nu, u)
    print(f"\n=== Beslut för {idag} kl 20 UTC ===\n  {beslut} — {varfor}")
    print(f"  senaste krönika: {senaste_kronika()}")
    print(f"\n=== Källor till högerkolumnen ({len(kallor(u))}) ===")
    for k in kallor(u):
        print(f"  [{k['date']}] {k['headline'][:70]}")
    print("\n=== Prompt ===")
    print(bygg_prompt(u)[:1500])
    print("\n=== Rubrikdelning ===")
    for prof in ["Veckan som gick i skärgården\n\nDet var en stilla vecka. *J.S.*",
                 "Ingen rubrikrad alls här, bara brödtext utan tom rad."]:
        print(f"  in:  {prof[:45]!r}\n  out: {dela_rubrik(prof, idag)}")
    print("\nSelftest OK.")
