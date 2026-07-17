#!/usr/bin/env python3
"""
Veckoreflektion — Julius lär sig varje vecka.

Separat från den dagliga genereringen (som lämnas orörd). Detta script:
  1. Läser de senaste 7 dagarnas artiklar
  2. Mäter: trope-frekvens, meningslängd, vilka riktlinjer som brutits
  3. Föreslår nya undvik/rotera-tillägg (med bevis)
  4. Skriver ett veckobrev + loggar till riktlinjer.json["veckoreflektioner"]

Mänsklig tid: ~5 min — du läser veckobrevet och flyttar godkända förslag
till "undvik"/"rotera" (redigera riktlinjer.json). Prompten läser bara
godkända listor, så ingenting ändras utan ditt godkännande.

Kör veckovis via GitHub Actions eller manuellt:
    python reflect_week.py
"""
import os
import re
import json
import glob
import datetime
import statistics as st
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RIKTLINJER_PATH = os.path.join(HERE, "riktlinjer.json")
ARTICLES_GLOB = os.path.join(HERE, "src", "content", "articles", "*.json")

# Fraser att bevaka (från riktlinjerna + kända klichéer)
WATCH_TROPES = [
    "månget öga har", "månget öga", "låtom oss", "sannerligen", "i sanning",
    "må vi", "vårt folk", "vårt land", "vår plikt", "månne", "mången",
]


def load_recent(days: int = 7):
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    out = []
    for f in glob.glob(ARTICLES_GLOB):
        name = os.path.basename(f)
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
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
        text = ((data.get("julius_text") or "") + " " +
                (data.get("julius_headline") or "")).lower()
        out.append({"file": name, "date": m.group(1), "text": text})
    return out


def measure(articles):
    trope_counts = Counter()
    sentence_lens = []
    for a in articles:
        for tr in WATCH_TROPES:
            c = a["text"].count(tr)
            if c:
                trope_counts[tr] += c
        for s in re.split(r"[.!?]+", a["text"]):
            w = s.split()
            if len(w) > 2:
                sentence_lens.append(len(w))
    stats = {}
    if sentence_lens:
        stats = {
            "median": int(st.median(sentence_lens)),
            "mean": round(st.mean(sentence_lens), 1),
            "stdev": round(st.pstdev(sentence_lens), 1),
            "over35_pct": round(100 * sum(1 for x in sentence_lens if x > 35) / len(sentence_lens)),
        }
    return trope_counts, stats, len(articles)


def propose_additions(trope_counts, n_articles):
    """Föreslå fraser att undvika/rotera utifrån frekvens."""
    proposals = []
    for tr, count in trope_counts.most_common():
        per_article = count / max(n_articles, 1)
        if per_article >= 0.5 and tr not in ("icke", "skall"):  # kärregister — rotera ej bannlys
            proposals.append({
                "fras": tr,
                "bevis": {"frekvens_total": count, "per_artikel": round(per_article, 2)},
                "forslag": "rotera" if per_article < 1.0 else "undvik",
                "anledning": f"{count} förekomster över {n_articles} artiklar "
                             f"({round(per_article*100)}% av texter).",
            })
    return proposals


def main():
    articles = load_recent(7)
    if not articles:
        print("Inga artiklar de senaste 7 dagarna — inget att reflektera över.")
        return

    trope_counts, sentence_stats, n = measure(articles)
    proposals = propose_additions(trope_counts, n)

    # Ladda nuvarande riktlinjer för att visa vad som redan finns
    with open(RIKTLINJER_PATH, "r", encoding="utf-8") as f:
        riktlinjer = json.load(f)

    existerande = {r["fras"] for r in riktlinjer.get("undvik", [])} | \
                  {r["fras"] for r in riktlinjer.get("rotera", [])}
    nya = [p for p in proposals if p["fras"] not in existerande]

    veckobrev = []
    veckobrev.append(f"VECKOBREV — vecka {datetime.date.today().isocalendar()[1]}")
    veckobrev.append(f"Artiklar denna vecka: {n}")
    veckobrev.append("")
    veckobrev.append("MÄTTA KLICHÉER (frekvens denna vecka):")
    for tr, c in trope_counts.most_common(10):
        flag = "  <-- redan på listan" if tr in existerande else ""
        veckobrev.append(f"  {c:4d}  {tr}{flag}")
    if sentence_stats:
        veckobrev.append("")
        veckobrev.append("MENINGSlängd:")
        veckobrev.append(
            f"  median {sentence_stats['median']} | mean {sentence_stats['mean']} | "
            f"stdev {sentence_stats['stdev']} | >35 ord: {sentence_stats['over35_pct']}%"
        )
        if sentence_stats["stdev"] < 12:
            veckobrev.append("  ⚠ För enhetlig — stilmålet 'variera meningslängd' ej nått.")
    veckobrev.append("")
    veckobrev.append("NYA FÖRSLAG (godkänn genom att flytta till undvik/rotera i riktlinjer.json):")
    if nya:
        for p in nya:
            veckobrev.append(f"  [{p['forslag']}] \"{p['fras']}\" — {p['anledning']}")
    else:
        veckobrev.append("  (inga nya — nuvarande riktlinjer håller)")

    brev = "\n".join(veckobrev)
    print(brev)

    # Logga reflektionen (ändrar ej undvik/rotera — det kräver ditt godkännande)
    entry = {
        "datum": datetime.date.today().isoformat(),
        "artiklar": n,
        "troper": dict(trope_counts.most_common(10)),
        "meningslangd": sentence_stats,
        "nya_forslag": nya,
    }
    riktlinjer.setdefault("veckoreflektioner", []).append(entry)
    riktlinjer["updated"] = datetime.date.today().isoformat()
    with open(RIKTLINJER_PATH, "w", encoding="utf-8") as f:
        json.dump(riktlinjer, f, ensure_ascii=False, indent=2)
    print("\n(Loggad till riktlinjer.json['veckoreflektioner']. Undvikslistan oförändrad — väntar godkännande.)")


if __name__ == "__main__":
    main()
