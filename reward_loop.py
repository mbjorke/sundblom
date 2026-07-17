#!/usr/bin/env python3
"""
Belöningsloop (regelbaserad, spektrum nivå 1 — noll API-kostnad).

Körs mot veckans data som demo. Belöning = vad som kommer ihåg (guld-exempel);
bestraffning = vad som undertrycks (undvik-lista + vikthöjning).

Belöningsfunktionen är transparent och bygger på projektets mål:
  - efterlevnad av riktlinjer (inga undvik-fraser, låtom oss max 1) → starkast
  - meningsrytm (variation i längd) → belönas
  - stilistisk konsistens (signatur, versalrubrik, nyckelfraser) → belönas
  - återhållsam längd (120–250 ord) → belönas

NOTERA: komik/anakroni MINIMERAS inte — det är personans poäng. Den visas
som lager men är inte ett straff-mål. Straffet riktas mot klichéer och
mekanisk enhetlighet, inte mot själva rösten.

Kör:  python reward_loop.py [dagar]
"""
import os
import re
import sys
import json
import glob
import datetime
import statistics as st
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RIKTLINJER_PATH = os.path.join(HERE, "riktlinjer.json")
EXEMPEL_PATH = os.path.join(HERE, "exempelbibliotek.json")
STRAFF_PATH = os.path.join(HERE, "straff_logg.json")
ARTICLES_GLOB = os.path.join(HERE, "src", "content", "articles", "*.json")

KEY_PHRASES = [
    "fäderneärvda", "självstyrelsens heliga grundvalar", "låtom oss icke vika",
    "det åländska folkets oförytterliga rätt", "fastlandets godtycke",
]


def load_recent(days: int):
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    out = []
    for f in glob.glob(ARTICLES_GLOB):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if not m:
            continue
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            data = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"file": os.path.basename(f), "date": m.group(1), "data": data})
    return out


def analyze(article):
    """Beräkna belöningspoäng + underliggande signaler för en artikel."""
    text = article["data"].get("julius_text") or ""
    body = article["data"].get("body") or ""
    text_low = text.lower()
    lines = [l for l in text.split("\n") if l.strip()]
    headline = lines[0] if lines else ""

    # riktlinjer (ladda undvik/rotera)
    with open(RIKTLINJER_PATH, "r", encoding="utf-8") as fh:
        rikt = json.load(fh)
    undvik = [r["fras"] for r in rikt.get("undvik", [])]
    rotera = [r["fras"] for r in rikt.get("rotera", [])]

    undvik_hits = {p: text_low.count(p.lower()) for p in undvik if p.lower() in text_low}
    latom_oss = text_low.count("låtom oss")

    # meningsrytm
    sentences = [s.split() for s in re.split(r"[.!?]+", text) if len(s.split()) > 2]
    slens = [len(s) for s in sentences]
    slen_stdev = st.pstdev(slens) if len(slens) > 1 else 0
    has_short = any(x < 12 for x in slens)
    has_long = any(x > 35 for x in slens)

    # stilistisk konsistens
    has_sig = "*J.S.*" in text
    has_caps = bool(headline) and headline == headline.upper() and any(c.isalpha() for c in headline)
    key_hits = sum(1 for p in KEY_PHRASES if p in text_low)

    word_count = len(text.split())
    body_words = len(body.split())
    length_ratio = word_count / body_words if body_words else 1.0

    # ── belöningsfunktion (transparent) ──
    # Baslinje 50 (neutral). Undvik-fraser är en HÅRD signal: drar ner kraftigt
    # och stänger ute från guld-exempel (se hard gate nedan).
    score = 50.0
    reasons = []
    # bestraffning: undvik-fraser (starkast signal — klichéer diskvalificerar)
    for p, c in undvik_hits.items():
        score -= 30 * c
        reasons.append(f"−{30*c} undvik-fras '{p}' ×{c}")
    # bestraffning: låtom oss överanvändning
    if latom_oss > 1:
        pen = 10 * (latom_oss - 1)
        score -= pen
        reasons.append(f"−{pen} 'låtom oss' ×{latom_oss} (max 1)")
    # belöning: meningsrytm
    score += min(slen_stdev, 14)
    reasons.append(f"+{min(slen_stdev,14):.0f} meningsvariation (stdev {slen_stdev:.1f})")
    if has_short and has_long:
        score += 6
        reasons.append("+6 kort+long rytm")
    # belöning: konsistens
    if has_sig:
        score += 8; reasons.append("+8 signatur")
    if has_caps:
        score += 8; reasons.append("+8 versalrubrik")
    if key_hits >= 2:
        score += 10; reasons.append(f"+{10} nyckelfraser ×{key_hits}")
    elif key_hits >= 1:
        score += 5; reasons.append(f"+5 nyckelfras ×{key_hits}")
    # belöning: återhållsam längd
    if 120 <= word_count <= 250:
        score += 12; reasons.append("+12 återhållsam längd")
    elif word_count < 80 or word_count > 300:
        score -= 8; reasons.append("−8 olämplig längd")

    score = max(0, min(100, score))
    return {
        "score": round(score, 1),
        "reasons": reasons,
        "undvik_hits": undvik_hits,
        "latom_oss": latom_oss,
        "slen_stdev": round(slen_stdev, 1),
        "word_count": word_count,
        "length_ratio": round(length_ratio, 2),
        "headline": headline,
        "excerpt": text[:160].replace("\n", " "),
    }


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    articles = load_recent(days)
    if not articles:
        print(f"Inga artiklar de senaste {days} dagarna.")
        return

    scored = []
    for a in articles:
        s = analyze(a)
        s["id"] = a["file"]
        s["date"] = a["date"]
        scored.append(s)
    scored.sort(key=lambda x: x["score"], reverse=True)

    n = len(scored)
    # HÅRD GRIND: guld-exempel får inte innehålla någon undvik-fras (klichéer
    # diskvalificerar från förstärkning). Om nästan ingen kvalificerar är det
    # i sig en ärlig signal — klichén är allöver, och det motiverar bestraffningen.
    eligible = [s for s in scored if not s["undvik_hits"]]
    top_n = max(3, n // 20)
    bot_n = max(3, n // 20)
    rewarded = eligible[:top_n]
    punished = scored[-bot_n:]

    # ── BELÖNING: skriv guld-exempel (minne som förstärks via few-shot) ──
    library = []
    if os.path.exists(EXEMPEL_PATH):
        try:
            library = json.load(open(EXEMPEL_PATH, encoding="utf-8"))
        except json.JSONDecodeError:
            library = []
    existing_ids = {e["id"] for e in library}
    new_gold = []
    for r in rewarded:
        if r["id"] not in existing_ids:
            new_gold.append({
                "id": r["id"], "date": r["date"], "score": r["score"],
                "headline": r["headline"], "excerpt": r["excerpt"],
                "reason": "Hög efterlevnad + varierad rytm",
            })
    library.extend(new_gold)
    with open(EXEMPEL_PATH, "w", encoding="utf-8") as fh:
        json.dump(library, fh, ensure_ascii=False, indent=2)

    # ── BESTRAFFNING: logga + föreslå undvik-tillägg (ej skriver över godkänd lista) ──
    violation_counter = Counter()
    punished_log = []
    for p in punished:
        for phrase, c in p["undvik_hits"].items():
            violation_counter[phrase] += c
        punished_log.append({
            "id": p["id"], "date": p["date"], "score": p["score"],
            "headline": p["headline"], "violations": p["undvik_hits"],
            "latom_oss": p["latom_oss"], "slen_stdev": p["slen_stdev"],
        })
    straff = {"date": datetime.date.today().isoformat(),
              "punished": punished_log,
              "proposed_undvik_additions": [
                  {"fras": p, "forekomster": c, "action": "undvik"} for p, c in violation_counter.most_common()
              ]}
    with open(STRAFF_PATH, "w", encoding="utf-8") as fh:
        json.dump(straff, fh, ensure_ascii=False, indent=2)

    # ── RAPPORT ──
    print("=" * 64)
    print(f"BELÖNINGSLOOP — de senaste {days} dagarna ({n} artiklar)")
    print("=" * 64)
    scores = [s["score"] for s in scored]
    print(f"Poäng: min {min(scores):.0f} | median {st.median(scores):.0f} | max {max(scores):.0f} | snitt {st.mean(scores):.1f}")
    print(f"Klichéfria (guld-kvalificerade): {len(eligible)} av {n}")
    print(f"Belönade (→ exempelbibliotek): {len(new_gold)} nya (totalt {len(library)})")
    print(f"Bestraffade (→ strafflogg): {len(punished)}")
    if len(eligible) < top_n:
        print(f"⚠ Bara {len(eligible)} klichéfria — guld-exempelbiblioteket växte inte fullt ut. Klichén är allöver.")
    print()
    print("── BELÖNADE (guld-exempel, förstärks) ──")
    for r in rewarded[:5]:
        print(f"  {r['score']:5.1f}  {r['date']}  {r['headline'][:48]}")
        print(f"         {', '.join(r['reasons'][:3])}")
    print()
    print("── BESTRAFFADE (flaggade, mönster → undvik) ──")
    for p in punished[:5]:
        v = ', '.join(p['undvik_hits'].keys()) or 'ingen kliché (olämplig längd/rytm)'
        print(f"  {p['score']:5.1f}  {p['date']}  {p['headline'][:42]}")
        print(f"         straff: {v}  | låtom oss:{p['latom_oss']} stdev:{p['slen_stdev']}")
    print()
    print("── FÖRESLAGNA undvik-tillägg (väntar godkännande) ──")
    if straff["proposed_undvik_additions"]:
        for a in straff["proposed_undvik_additions"]:
            print(f"  [undvik] \"{a['fras']}\" — {a['forekomster']} förekomster i bestraffade")
    else:
        print("  (inga nya — nuvarande undvik-lista täcker")
    print()
    print("Skrivit: exempelbibliotek.json (belöningsminne) + straff_logg.json (bestraffningslogg)")
    print("Undvik-listan i riktlinjer.json EJ ändrad — väntar ditt godkännande.")


if __name__ == "__main__":
    main()
