#!/usr/bin/env python3
"""reflect.py — Julius Sundblom reflekterar fristående ur minne, inte ur nyhetsflödet.

Triggas söndagar (eller manuellt). Läser Julius inre minne (riktlinjer, guld-exempel,
straffade texter, senaste veckoreflektioner, senaste artiklar, återkommande teman) och
låter honom skriva en fristående betraktelse i ett av tre lägen:

  traddragning  — ett tema som återkommit i hans skrivande; varför dyker det upp?
  omprövning    — läs en gammal ledare och svara på sitt forna jag
  iakttagelse   — plats och årstid, utan koppling till nyhet

Målet: gå från reaktiv (artikel → ledare) till självinitierat tänkande. Reflektionen
får ingen källartikel — den genereras från minne och internt tillstånd.

Användning:
  python reflect.py             # kör (säkerhetskontroll + generera + pusha)
  python reflect.py --dry-run    # planera + generera, men pusha ej
  python reflect.py --selftest   # validera minnesladdning/prompt utan API-nyckel
  python reflect.py --mode omprövning   # tvinga läge
  python reflect.py --force      # tillåt ytterligare reflektion samma dag
"""
from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import logging
import os
import re
import sys

from google import genai
from google.genai import types as genai_types

import main as M  # återanvänd _push_file, slugify, load_riktlinjer, SUNDBLOM_PROMPT, env

HERE = os.path.dirname(os.path.abspath(__file__))
ARTICLES_GLOB = os.path.join(HERE, "src", "content", "articles", "*.json")
RIKTLINJER_PATH = os.path.join(HERE, "riktlinjer.json")
EXEMPEL_PATH = os.path.join(HERE, "exempelbibliotek.json")
STRAFF_PATH = os.path.join(HERE, "straff_logg.json")
SELECTOR_LOG_PATH = os.path.join(HERE, "arkiv", "selector_logg.json")
STATE_PATH = "arkiv/reflection_state.json"  # relativt repo-rot; pushas via API

# Modell för reflektion. Default = flash med thinking på (till skillnad från dagens thinking_budget=0).
# gemini-2.5-pro är 404 för denna nyckel. Sätt SUNDBLOM_REFLECTION_MODEL för att byta.
SUNDBLOM_REFLECTION_MODEL = os.environ.get("SUNDBLOM_REFLECTION_MODEL") or "gemini-2.5-flash"
# Thinking-budget för reflektionen (0 = av; >0 = tänkande på)
# max_output_tokens måste vara thinking_budget + response-budget (thinking ingår i max_output_tokens)
SUNDBLOM_REFLECTION_THINKING = int(os.environ.get("SUNDBLOM_REFLECTION_THINKING", "4096"))
SUNDBLOM_REFLECTION_MAX_TOKENS = int(os.environ.get("SUNDBLOM_REFLECTION_MAX_TOKENS", "8192"))

log = logging.getLogger("reflect")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

MODES = ("tråddragning", "omprövning", "iakttagelse")
MODE_DESC = {
    "tråddragning": (
        "TRÅDDRAGNING. Du har märkt att ett visst tema återvänt i dina ledare under "
        "senaste tiden. Drag i den tråden — varför återkommer det, vad säger mönstret "
        "om vår tid, varifrån kommer oron? Skriv betraktelsen kring det återkommande."
    ),
    "omprövning": (
        "OMPRÖVNING. Läs (på nytt) en av dina egna tidigare ledare som anges i minnet. "
        "Svara på ditt forna jag — håller du fast vid den hållningen, eller har tiden "
        "och eftertanke luckrat upp den? En man som tänker omprövar sig med värdighet."
    ),
    "iakttagelse": (
        "IAKTTAGELSE. Skriv en betraktelse om plats och årstid — Åland, havet, ljuset, "
        "vad som förändrats sedan våren — utan koppling till någon specifik nyhet. "
        "Låt iakttagelsen bära en tanke om förändring och bestånd."
    ),
}

# Personan: återanvänd huvudet (världssyn + språkliga krav) från main.py, byt ut
# artikel-specifika delar mot reflektions-guidance.
_PERSONA_HEAD = M.SUNDBLOM_PROMPT.split("FAKTAKRAV (strikt):")[0]

# Användar-prompt (uppgift + minne). System-prompten är _PERSONA_HEAD (personan).
REFLECTION_PROMPT = """DENNA UPPGIFT — FRISTÅENDE BETRAKTELSE (ej en nyhetskommentar):
Du skriver inte om en enskild nyhet idag. Du skriver en fristående betraktelse ur ditt
inre minne — det du tänkt på, mönster du sett, en tidigare hållning du vill ompröva,
eller en iakttagelse om plats och årstid. Detta är din röst på egen hand, inte en reaktion
på en nyhet. Ingen artikel har lagts fram för dig.

LÄGE ATT SKRIVA I:
{mode_desc}

MINNE TILL FÖRFOGANDE (det du skrivit och lärt hittills — dra ur detta):
{memory}

FAKTAKRAV (strikt):
- Skriv ENBART utifrån vad som finns i ditt minne ovan och din allmänna historiska
  kunskap som Sundblom (1920-talets Åland, självstyrelsen, 1921-beslutet,
  Nationernas Förbund, inbördeskriget 1918).
- Hitta inte på specifika händelser, personer eller nyheter som inte finns i minnet.
- I iakttagelse-läget må du använda årstid och plats allmänt, men inga specifika
  väderleksrapporter eller nyhetshändelser.

RÖST OCH TON:
- Detta är en offentlig betraktelse i tidningen — värdig, inte en privat dagbok.
- Tvivel och omprövning är tillåtet och välkomnat — en tänkande man omprövar sig.
  Men uttryck det i 1920-talets register, icke modernt terapi-språk.
- Visa att du tänker: drag trådar, gör kopplingar över tid, stundom tvekande,
  stundom stridbar. Det skall märkas att något rör sig inuti.

FÖRBJUDET SPRÅK (strikt — bryter mot din persona):
- Nämn ALDRIG: AI, skript, dashboard, mätvärden, modell, prompt, JSON, "projektet",
  algorimer, kod, automation, eller att du är "genererad" eller "maskin".
- Du äro Julius Sundblom, punkt. Inget meta.

INNEHÅLLSKRAV:
- Inled med en dramatisk rubrik (versaler, utan citattecken).
- Koppla till din världssyn där det naturligt passar (autonomi, sedlighet, ordning).
- LÄNGD: 3–4 korta stycken. Max 250 ord.
- Avsluta alltid med signaturen: *J.S.*

Svara ENBART med den färdiga texten (rubrik + stycken + signatur). Ingen förklaring, ingen inledning."""


# ─────────────────────────────────────────────────────────────────────────────
# API-anrop
# ─────────────────────────────────────────────────────────────────────────────

# Singleton-klient (undviker httpx "client has been closed" vid GC).
_client_instance: "genai.Client | None" = None


def _client() -> "genai.Client":
    global _client_instance
    if not M.GOOGLE_API_KEY:
        raise EnvironmentError("GOOGLE_API_KEY saknas.")
    if _client_instance is None:
        _client_instance = genai.Client(api_key=M.GOOGLE_API_KEY)
    return _client_instance


def _plan(system: str, user: str) -> dict:
    """Planerar läge via ett litet JSON-anrop (thinking avslaget)."""
    resp = _client().models.generate_content(
        model=SUNDBLOM_REFLECTION_MODEL,
        contents=user,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=512,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
        ),
    )
    return _parse_json(resp.text)


def _generate(system: str, user: str) -> str:
    """Genererar reflektionen. Thinking på (thinking_budget>0) för äkta eftertanke."""
    try:
        resp = _client().models.generate_content(
            model=SUNDBLOM_REFLECTION_MODEL,
            contents=user,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=SUNDBLOM_REFLECTION_MAX_TOKENS,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=SUNDBLOM_REFLECTION_THINKING),
            ),
        )
    except Exception as e:
        log.warning("Thinking-budget %d misslyckades (%s) — faller tillbaka på thinking=0", SUNDBLOM_REFLECTION_THINKING, str(e)[:80])
        resp = _client().models.generate_content(
            model=SUNDBLOM_REFLECTION_MODEL,
            contents=user,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=SUNDBLOM_REFLECTION_MAX_TOKENS,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
    usage = resp.usage_metadata
    if usage:
        log.info("Tokens: %d in + %d out", usage.prompt_token_count, usage.candidates_token_count)
    return resp.text.strip()


def _parse_json(text: str) -> dict:
    """Tolerant JSON-tolkning (hanterar inbäddad JSON i markdown-fence)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


# ─────────────────────────────────────────────────────────────────────────────
# Minne
# ─────────────────────────────────────────────────────────────────────────────

def _season_label(d: datetime.date) -> str:
    m = d.month
    if m in (12, 1, 2):
        return "vinter"
    if m in (3, 4, 5):
        return "vår"
    if m in (6, 7, 8):
        return "sommar"
    return "höst"


def _load_articles_summary(days: int = 60, limit: int = 22) -> list[dict]:
    """Senaste artiklarna (datum, rubrik, utdrag) — ej reflektioner."""
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    out = []
    for f in glob.glob(ARTICLES_GLOB):
        try:
            a = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if a.get("content_type") == "reflektion":
            continue
        d = a.get("date", "")
        try:
            dd = datetime.date.fromisoformat(d[:10])
        except ValueError:
            continue
        if dd < cutoff:
            continue
        out.append({"date": d, "headline": a.get("headline", ""), "excerpt": (a.get("julius_text") or "")[:160]})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:limit]


def _load_gold(n: int = 7) -> list[dict]:
    try:
        b = json.load(open(EXEMPEL_PATH, encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return []
    b = b if isinstance(b, list) else b.get("exemplar", b.get("guld", []))
    return b[:n]


def _load_punished(n: int = 5) -> list[dict]:
    try:
        s = json.load(open(STRAFF_PATH, encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return []
    p = s.get("punished", []) if isinstance(s, dict) else s
    return p[:n]


def _load_recent_reflections_state() -> dict:
    """Läser reflection_state.json lokalt (finns i utcheckningen om tidigare kör pushat)."""
    local = os.path.join(HERE, "arkiv", "reflection_state.json")
    if not os.path.exists(local):
        return {}
    try:
        return json.load(open(local, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _theme_counts() -> list[tuple[str, int]]:
    """Återkommande teman från selector_logg (senaste 60 dagar)."""
    if not os.path.exists(SELECTOR_LOG_PATH):
        return []
    try:
        entries = json.load(open(SELECTOR_LOG_PATH, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    cutoff = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    c = collections.Counter()
    for e in entries:
        if (e.get("date") or "") >= cutoff and e.get("skriv"):
            t = e.get("tema") or "okänt"
            for part in re.split(r"[,;]| och ", t):
                part = part.strip()
                if part:
                    c[part] += 1
    return c.most_common(6)


def _pick_revisitation_candidates(state: dict) -> dict:
    """Väljer 1 stolt (guld) + 1 problematisk (straffad) ledare, 30–120 dagar gammal."""
    today = datetime.date.today()
    lo = today - datetime.timedelta(days=120)
    hi = today - datetime.timedelta(days=30)
    revisited = set(state.get("revisited_ids", []))
    gold_ids = {g.get("id") for g in _load_gold(15)}
    punished = _load_punished(15)
    punished_ids = {p.get("id") for p in punished if p.get("id")}
    proud = problematic = None
    cands = []
    for f in glob.glob(ARTICLES_GLOB):
        base = os.path.basename(f)
        if base in revisited:
            continue
        try:
            a = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if a.get("content_type") == "reflektion":
            continue
        try:
            dd = datetime.date.fromisoformat(a.get("date", "")[:10])
        except ValueError:
            continue
        if not (lo <= dd <= hi):
            continue
        cands.append((f, base, a, dd))
    # stolt: guld-id match
    for f, base, a, dd in cands:
        if base in gold_ids and not proud:
            proud = {"id": base, "date": str(dd), "headline": a.get("headline", ""), "excerpt": (a.get("julius_text") or "")[:220]}
            break
    # problematisk: straffad
    for f, base, a, dd in cands:
        if base in punished_ids and not problematic:
            problematic = {"id": base, "date": str(dd), "headline": a.get("headline", ""), "excerpt": (a.get("julius_text") or "")[:220]}
            break
    # fallback: äldst tillgänglig
    if not proud and cands:
        f, base, a, dd = cands[-1]
        proud = {"id": base, "date": str(dd), "headline": a.get("headline", ""), "excerpt": (a.get("julius_text") or "")[:220]}
    return {"proud": proud, "problematic": problematic}


def build_memory(state: dict) -> tuple[str, list[str]]:
    """Bygger minnesblocket för prompten. Returnerar (text, memory_refs)."""
    today = datetime.date.today()
    refs = []
    parts = [f"DAGENS DATUM: {today.isoformat()} ({_season_label(today)}, Åland).\n"]

    # Stilriktlinjer (undvik/rotera/stilmål) — långsiktigt minne
    rikt = M.load_riktlinjer()
    if rikt:
        parts.append("DINA STILRIKTLINJER (långsiktigt minne):")
        parts.append(rikt.strip())
        parts.append("")

    # Senaste veckoreflektioner (egna mätningar)
    try:
        r = json.load(open(RIKTLINJER_PATH, encoding="utf-8"))
        vr = r.get("veckoreflektioner", [])[-3:]
        if vr:
            parts.append("DINA SENASTE VECKOREFLEKTIONER (vad du mätt hos dig själv):")
            for e in vr:
                troper = e.get("troper", {})
                top = ", ".join(f"{k}:{v}" for k, v in list(troper.items())[:4]) or "—"
                ml = e.get("meningslangd", {})
                u = e.get("urval") or {}
                parts.append(f"  {e.get('datum')}: artiklar {e.get('artiklar')}, klichéer [{top}], meningslängd stdev {ml.get('stdev')}, urval {u.get('godkande_pct','?')}% godkända.")
                refs.append(f"veckoreflektion {e.get('datum')}")
            parts.append("")
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        pass

    # Guld-exempel (stolt över)
    gold = _load_gold(7)
    if gold:
        parts.append("DINA BÄSTA LEDARE (guld-exempel du kan vara stolt över):")
        for g in gold:
            parts.append(f"  [{g.get('date')}] {g.get('headline','')[:70]} — {g.get('reason','')}. Utdrag: {(g.get('excerpt') or '')[:180]}")
            refs.append(f"guld {g.get('id')}")
        parts.append("")

    # Straffade (problematiska)
    pun = _load_punished(5)
    if pun:
        parts.append("LEDARE DU SJÄLV/LOOPEN DÖMT SVAGA (klichétunga, att lära av):")
        for p in pun:
            parts.append(f"  [{p.get('date')}] {p.get('headline','')[:70]} — {p.get('reason', p.get('anledning',''))}")
            refs.append(f"straffad {p.get('id')}")
        parts.append("")

    # Senaste artiklarna (vad som fyllt hans dagar)
    arts = _load_articles_summary(60, 22)
    if arts:
        parts.append("SENASTE LEDARNA DU SKRIVIT (datum | rubrik | utdrag):")
        for a in arts:
            parts.append(f"  {a['date']} | {a['headline'][:60]} | {a['excerpt'][:120]}")
            refs.append(f"artikel {a['date']}")
        parts.append("")

    # Återkommande teman
    tc = _theme_counts()
    if tc:
        parts.append("ÅTERKOMMANDE TEMAN I DET DU GODKÄNT ATT SKRIVA OM: " + ", ".join(f"{t} ({c})" for t, c in tc))
        parts.append("")

    # Revisitation-kandidater
    rev = _pick_revisitation_candidates(state)
    if rev.get("proud") or rev.get("problematic"):
        parts.append("VID OMPRÖVNING — LEDARE ATT ÅTERLÄSA OCH BESVARA:")
        if rev.get("proud"):
            p = rev["proud"]
            parts.append(f"  (stolt) [{p['date']}] {p['headline'][:70]}: {p['excerpt']}")
            refs.append(f"revisit-proud {p['id']}")
        if rev.get("problematic"):
            p = rev["problematic"]
            parts.append(f"  (problematisk) [{p['date']}] {p['headline'][:70]}: {p['excerpt']}")
            refs.append(f"revisit-problem {p['id']}")
        parts.append("")

    return "\n".join(parts), refs


# ─────────────────────────────────────────────────────────────────────────────
# Lägesval (planerare)
# ─────────────────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = (
    "Du planerar Julius Sundbloms veckobetraktelse. Välj det läge som är mest fruktbart "
    "utifrån vad i minnet som rör sig. Tre lägen:\n"
    "  traddragning — ett återkommande tema att dra i\n"
    "  omprövning — besvara en tidigare egen ledare (endast om minnet innehåller "
    "'VID OMPRÖVNING'-kandidater)\n"
    "  iakttagelse — plats och årstid\n"
    "Svara ENBART med JSON: {\"mode\": ..., \"focus\": ..., \"why_now\": ..., \"public_headline\": ...}. "
    "mode måste vara exakt ett av: traddragning, omprövning, iakttagelse."
)


def choose_mode(memory: str, state: dict, forced: str | None) -> dict:
    if forced:
        if forced not in MODES:
            raise ValueError(f"Ogiltigt läge '{forced}'. Tillåtna: {MODES}")
        return {"mode": forced, "focus": "(tvingat läge)", "why_now": "manuellt valt", "public_headline": ""}
    recent_modes = state.get("recent_modes", [])
    last_focus = state.get("last_focus", "")
    omprv_available = "VID OMPRÖVNING" in memory
    avail = [m for m in MODES if not (m == "omprövning" and not omprv_available)]
    user = (
        f"MINNE:\n{memory[:3500]}\n\n"
        f"Tidigare lägen (undvik upprepning om möjligt): {recent_modes[-3:] or 'inga'}.\n"
        f"Förra fokuset: {last_focus[:80]}\n"
        f"Tillgängliga lägen: {avail}.\n"
        "Välj läge och fokus. public_headline är en arbetsrubrik (ej den slutgiltiga)."
    )
    plan = _plan(PLANNER_SYSTEM, user)
    if plan.get("mode") not in MODES or (plan["mode"] == "omprövning" and not omprv_available):
        log.warning("Planerare valde ogiltigt '%s' — faller tillbaka på traddragning.", plan.get("mode"))
        plan["mode"] = "traddragning"
    # cooldown: ej samma läge 3 gånger i rad
    if recent_modes[-2:] == [plan["mode"], plan["mode"]]:
        alt = next((m for m in MODES if m != plan["mode"] and (m != "omprövning" or omprv_available)), "traddragning")
        log.warning("Läge %s tre gånger i rad — byter till %s.", plan["mode"], alt)
        plan["mode"] = alt
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Spara
# ─────────────────────────────────────────────────────────────────────────────

def save_reflection(headline: str, julius_text: str, mode: str, focus: str,
                    memory_refs: list[str], date_iso: str) -> str:
    slug = M.slugify(headline) or f"reflektion-{date_iso}"
    article = {
        "headline": headline,
        "julius_text": julius_text,
        "body": "",                      # ingen källartikel
        "author": "Julius Sundblom",
        "source_url": "",                # självinitierat
        "source": "Julius minne",
        "date": date_iso,
        "published_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slug": slug,
        "content_type": "reflektion",
        "self_initiated": True,
        "reflection_mode": mode,
        "reflection_focus": focus,
        "memory_refs": memory_refs,
    }
    content = json.dumps(article, ensure_ascii=False, indent=2)
    path = f"src/content/articles/{date_iso}-{slug}.json"
    url = M._push_file(path, content, f"[skip ci] 🪞 Reflektion ({mode}): {headline[:55]}")
    return url


def save_state(state: dict) -> None:
    content = json.dumps(state, ensure_ascii=False, indent=2)
    try:
        M._push_file(STATE_PATH, content, "[skip ci] 🪞 reflection_state uppdaterad")
    except Exception as e:
        log.warning("Kunde inte pusha reflection_state: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Huvud
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Julius Sundblom reflekterar ur minne.")
    ap.add_argument("--selftest", action="store_true", help="validera minne/prompt utan API")
    ap.add_argument("--dry-run", action="store_true", help="generera men pusha ej")
    ap.add_argument("--mode", choices=MODES, help="tvinga läge")
    ap.add_argument("--force", action="store_true", help="tillåt extra reflektion samma dag")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    state = _load_recent_reflections_state()

    # Idempotens
    if state.get("last_date") == today and not args.force:
        log.warning("Reflektion för %s redan sparad. Använd --force för att köra igen.", today)
        return

    # Bygg minne
    memory, refs = build_memory(state)
    log.info("Minne byggt: %d tecken, %d referenser.", len(memory), len(refs))

    if args.selftest:
        plan = {"mode": args.mode or "tråddragning", "focus": "(selftest)", "why_now": "test", "public_headline": "TEST"}
        log.info("[selftest] läge: %s", plan["mode"])
        prompt_user = REFLECTION_PROMPT.format(mode_desc=MODE_DESC[plan["mode"]], memory=memory[:4000])
        log.info("[selftest] promptlängd: %d tecken. OK.", len(prompt_user))
        print("=== MINNESBLOCK (första 1500 tecken) ===")
        print(memory[:1500])
        return

    # Välj läge
    plan = choose_mode(memory, state, args.mode)
    mode = plan["mode"]
    focus = plan.get("focus", "")
    log.info("Läge: %s | fokus: %s | varför nu: %s", mode, focus[:60], plan.get("why_now", "")[:60])

    # Generera (system = personan, user = uppgift + minne — speglar main.py:s mönster)
    user = REFLECTION_PROMPT.format(mode_desc=MODE_DESC[mode], memory=memory[:6000])
    text = _generate(_PERSONA_HEAD, user)
    # rubrik = första raden; julius_text = HELA genererade texten (som main.py)
    lines = text.strip().splitlines()
    headline = lines[0].strip().lstrip("#").strip() if lines else f"Reflektion {today}"
    julius_text = text.strip()  # behåll rubrik + stycken + signatur intakta
    log.info("Reflektion genererad (%d tecken): %s", len(julius_text), headline[:60])

    if args.dry_run:
        print("=== GENERERAD REFLEKTION (dry-run) ===")
        print(text)
        return

    # Spara
    url = save_reflection(headline, julius_text, mode, focus, refs, today)
    log.info("Reflektion sparad: %s", url)

    # Trigga deployen. _push_file skriver bara till main, och CF Pages lyssnar
    # på deploy-branchen — utan detta blev reflektionen liggande osynlig ända
    # tills nästa ledare publicerades.
    M.save_last_headline(headline)

    # Uppdatera state
    recent_modes = (state.get("recent_modes", []) + [mode])[-6:]
    revisited = list(state.get("revisited_ids", []))
    for r in refs:
        if r.startswith("revisit-"):
            rid = r.split(" ", 1)[-1]
            if rid not in revisited:
                revisited.append(rid)
    revisited = revisited[-30:]
    new_state = {
        "last_date": today,
        "last_mode": mode,
        "last_focus": focus[:120],
        "recent_modes": recent_modes,
        "recent_themes": state.get("recent_themes", []),
        "revisited_ids": revisited,
    }
    save_state(new_state)
    log.info("Klar.")


if __name__ == "__main__":
    main()
