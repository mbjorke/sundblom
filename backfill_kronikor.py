#!/usr/bin/env python3
"""
Efterhandskrönikor — fyller tysta dagar som passerat innan krönikan fanns.

Krönikan infördes 26 september 2026. Dagarna dessförinnan som redaktörs-
omdömet lämnade tomma står kvar som hål i arkivet. Det här skriptet skriver
dem i efterhand, på exakt samma villkor som den dagliga körningen hade gjort
om koden funnits då.

Underlaget rekonstrueras troget: kronika.samla_underlag() filtrerar både
artiklar och selektorloggen på datum, så en krönika för den 12 september ser
bara det som fanns den 12 september — ingenting som hänt sedan dess.

Körs manuellt via workflows/backfill-kronikor.yml (där GOOGLE_API_KEY finns).

    python backfill_kronikor.py --dry-run            # visa beslut, skriv inget
    python backfill_kronikor.py --dagar 30           # leta tysta dagar en månad bak
    python backfill_kronikor.py --datum 2026-09-05 2026-09-12
"""

import os
import sys
import glob
import logging
import datetime
import argparse

import main as M
import kronika

log = logging.getLogger("backfill_kronikor")

# Krönikan fanns inte före detta datum — dagar efter det sköter den dagliga
# körningen själv, och ska inte efterhandsskrivas.
KRONIKAN_INFORD = "2026-09-26"
# Samma andemening som KRONIKA_MIN_DAYS i den dagliga körningen: en helg ska
# ge en krönika, inte två med nästan samma innehåll.
MIN_DAGAR_MELLAN = int(os.environ.get("KRONIKA_MIN_DAYS", "3"))
# Tidpunkten en efterhandskrönika stämplas med: kvällen den dag den avser.
PUBLICERAD_KLOCKAN = "T20:30:00Z"


def tysta_dagar(dagar_bak: int, till: str) -> list[str]:
    """Dagar utan publicerad utgåva inom fönstret, äldst först."""
    publicerade = {os.path.basename(p)[:10]
                   for p in glob.glob(os.path.join(M.ARTICLES_DIR, "*.json"))}
    slut = datetime.date.fromisoformat(till)
    return [
        d.isoformat()
        for d in (slut - datetime.timedelta(days=n) for n in range(dagar_bak, -1, -1))
        if d.isoformat() not in publicerade and d.isoformat() < KRONIKAN_INFORD
    ]


def gles_ut(dagar: list[str], min_gap: int = MIN_DAGAR_MELLAN) -> list[str]:
    """Behåller första dagen i varje klunga så två krönikor aldrig hamnar tätt."""
    valda: list[str] = []
    for d in dagar:
        if valda:
            avstand = (datetime.date.fromisoformat(d)
                       - datetime.date.fromisoformat(valda[-1])).days
            if avstand < min_gap:
                log.info("  %s hoppas över — %d dag(ar) efter %s", d, avstand, valda[-1])
                continue
        valda.append(d)
    return valda


def skriv_kronika_for(dag: str, dry_run: bool) -> bool:
    """Skriver en krönika daterad `dag`. Returnerar True om något sparades."""
    underlag = kronika.samla_underlag(dag)
    # Klockslaget är ointressant i efterhand — dagen är passerad och kan inte
    # längre bjuda på en nyhet — men beslutsfunktionen vill ha en tidpunkt.
    nu = datetime.datetime.fromisoformat(dag + "T20:30:00")
    beslut, varfor = kronika.bor_skriva_kronika(dag, nu, underlag)
    if not beslut:
        log.info("%s — avstår: %s", dag, varfor)
        return False

    kallor = kronika.kallor(underlag)
    log.info("%s — %s (%d källor i högerkolumnen)", dag, varfor, len(kallor))
    if dry_run:
        log.info("   [dry-run] genererar inte, sparar inte")
        return False

    try:
        rubrik, text = kronika.generera(
            underlag, M.SUNDBLOM_PROMPT, M.load_riktlinjer(), M._call_api
        )
    except ValueError as e:
        log.error("%s — krönikan förkastades: %s", dag, e)
        return False

    slug = f"{kronika.KRONIKA_SLUG_PREFIX}-{M.slugify(rubrik, max_length=48)}"
    M.save_article_json(
        headline=rubrik,
        julius_text=text,
        body="",
        author="Julius Sundblom",
        source_url=M.ALANDS_RADIO_URL,
        date_iso=dag,
        slug=slug,
        kind="kronika",
        sources=kallor,
        published_at=dag + PUBLICERAD_KLOCKAN,
    )
    log.info("%s — skriven: %s", dag, rubrik)
    return True


def main() -> None:
    """Väljer tysta dagar, skriver en krönika för var och en, deployar en gång."""
    ap = argparse.ArgumentParser(description="Skriver veckokrönikor i efterhand.")
    ap.add_argument("--dagar", type=int, default=30,
                    help="hur många dagar bakåt tysta dagar letas (default 30)")
    ap.add_argument("--datum", nargs="+",
                    help="skriv för exakt dessa datum i stället för att leta")
    ap.add_argument("--till", default=datetime.date.today().isoformat(),
                    help="fönstrets sista dag (default idag)")
    ap.add_argument("--alla", action="store_true",
                    help="skriv även dagar som ligger tätt (hoppar över glesningen)")
    ap.add_argument("--dry-run", action="store_true",
                    help="visa besluten utan att generera eller spara")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    if not args.dry_run and not M.GOOGLE_API_KEY:
        log.error("GOOGLE_API_KEY saknas — kan inte generera. Kör med --dry-run.")
        sys.exit(1)

    log.info("═══ Efterhandskrönikor ═══")
    kandidater = args.datum or tysta_dagar(args.dagar, args.till)
    if not kandidater:
        log.info("Inga tysta dagar i fönstret — inget att göra.")
        return
    log.info("Tysta dagar: %s", ", ".join(kandidater))

    valda = kandidater if (args.alla or args.datum) else gles_ut(kandidater)
    log.info("Skriver för: %s", ", ".join(valda) or "(inga)")

    skrivna = 0
    for dag in valda:
        if skriv_kronika_for(dag, args.dry_run):
            skrivna += 1

    if skrivna:
        # En enda deploy för hela omgången — inte en per krönika.
        if M.trigga_deploy(f"Efterhandskrönikor: {skrivna} st"):
            log.info("Deploy triggad.")
        else:
            log.error("Deploy misslyckades — nästa dagliga körning gör om försöket.")
    log.info("═══ Klart: %d krönika(or) skrivna. ═══", skrivna)


if __name__ == "__main__":
    main()
