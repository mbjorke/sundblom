#!/usr/bin/env python3
"""
Backfill-skript: genererar arkivposter för specifika artiklar med specifika datum.
Används via backfill.yml eller manuellt.

Rensar även gamla datumbaserade arkivfiler utan slug.
"""

import os
import sys
import json
import base64
import logging
import datetime
import requests

from main import (
    fetch_article_body,
    generate_sundblom,
    render_html,
    publish_archive_entry,
    rebuild_archive_index,
    generate_manifest,
    publish_to_github,
    generate_og_image,
    publish_og_image,
    publish_robots_txt,
    GITHUB_TOKEN,
    GITHUB_REPO,
    GITHUB_BRANCH,
    GITHUB_API_BASE,
    slugify,
    _push_file,
    log,
)

# ── Artiklar att backfilla ────────────────────────────────────────────────────
# Format: (datum YYYY-MM-DD, artikel-URL, rubrik om scrapers missar)
BACKFILL_ARTICLES = [
    (
        "2026-03-14",
        "https://alandsradio.ax/nyheter/foraldrar-flicklaget-behandlas-nedlatande-kallades-ackliga",
        "Föräldrar: flicklaget behandlas nedlåtande — kallades äckliga",
    ),
    (
        "2026-03-15",
        "https://alandsradio.ax/nyheter/alands-idrott-behover-tas-pa-allvar",
        "Ålands idrott behöver tas på allvar",
    ),
    (
        "2026-03-16",
        "https://alandsradio.ax/nyheter/eckerostyrelsens-fortroende-for-forvaltningen-ar-intakt-trots-inledd-utredning-om-tjanstefel",
        "Eckeröstyrelsens förtroende för förvaltningen är intakt trots inledd utredning om tjänstefel",
    ),
    (
        "2026-03-17",
        "https://alandsradio.ax/nyheter/kallbadhus-hopptorn-och-atoll-ska-byggas-vid-lilla-holmen",
        "Kallbadhus, hopptorn och atoll ska byggas vid Lilla holmen",
    ),
    (
        "2026-03-18",
        "https://alandsradio.ax/nyheter/hus-obeboeligt-efter-nattlig-brand-i-jomala",
        "Hus obeboligt efter nattlig brand i Jomala",
    ),
    (
        "2026-03-19",
        "https://alandsradio.ax/nyheter/sa-resonerar-alandska-arbetsgivare-om-att-anstalla-personer-med-extrema-asikter",
        "Så resonerar åländska arbetsgivare om att anställa personer med extrema åsikter",
    ),
]

# Gamla filer utan slug som ska raderas
FILES_TO_DELETE = [
    "arkiv/2026-03-18.html",
    "arkiv/2026-03-19.html",
    "arkiv/2026-03-19-flicklaget-behandlas-nedlatande.html",
]


def delete_file(path: str) -> None:
    """Raderar en fil från repot via GitHub API om den finns."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.warning("GitHub-variabler saknas, kan inte radera %s", path)
        return

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    get_resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})

    if get_resp.status_code == 404:
        log.info("Fil finns ej (hoppar över): %s", path)
        return
    get_resp.raise_for_status()
    sha = get_resp.json().get("sha")

    del_resp = requests.delete(api_url, headers=headers, json={
        "message": f"🗑️ Rensar gammal arkivfil: {path}",
        "sha": sha,
        "branch": GITHUB_BRANCH,
    })
    del_resp.raise_for_status()
    log.info("🗑️ Raderad: %s", path)


def extract_headline_from_url(url: str) -> str:
    """Extraherar rubrik ur URL-slug som fallback."""
    slug = url.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").capitalize()


def main() -> None:
    log.info("═══ Backfill startar — %d artiklar ═══", len(BACKFILL_ARTICLES))

    # 1. Radera gamla filer utan slug
    log.info("─── Rensar gamla arkivfiler ───")
    for path in FILES_TO_DELETE:
        delete_file(path)

    latest_html = None
    latest_date = ""
    latest_headline = ""

    # 2. Generera varje artikel
    for date_iso, url, fallback_headline in BACKFILL_ARTICLES:
        log.info("─── Processar %s: %s ───", date_iso, url)

        body, author = fetch_article_body(url)

        # Försök extrahera rubrik från sidans <title> eller h1 om body finns
        headline = fallback_headline
        if not body:
            log.warning("Inget innehåll hämtat — använder fallback-rubrik")

        julius_text = generate_sundblom(headline, url, body)
        html = render_html(headline, url, julius_text, body, author,
                           date_override=date_iso)

        publish_archive_entry(html, headline, date_override=date_iso)
        log.info("✅ Arkiverad: %s", date_iso)

        # Håll koll på den senaste (kronologiskt) för index.html
        if date_iso > latest_date:
            latest_date = date_iso
            latest_html = html
            latest_headline = headline

    # 3. Uppdatera index.html med senaste artikeln
    if latest_html:
        log.info("─── Uppdaterar index.html med senaste artikel (%s) ───", latest_date)
        publish_to_github(latest_html)

        # OG-bild baserad på senaste
        import os as _os
        with open("index.html", "w", encoding="utf-8") as f:
            f.write(latest_html)
        og_png = generate_og_image()
        if og_png:
            publish_og_image(og_png)

    # 4. Bygg arkivindex + manifest
    rebuild_archive_index()
    generate_manifest()

    # 5. robots.txt
    publish_robots_txt()

    log.info("═══ Backfill klar. ═══")


if __name__ == "__main__":
    main()
