#!/usr/bin/env python3
"""
Reparerar artiklar med trunkerat julius_text.
Läser alla JSONar i src/content/articles/, hittar de med kort julius_text,
genererar om och pushar till repot via GitHub API.

Kör via: python repair.py
Eller via GitHub Actions (repair.yml) med workflow_dispatch.
"""

import os
import sys
import json
import base64
import logging
import datetime
import requests

# Återanvänd funktioner från main
from main import (
    GOOGLE_API_KEY,
    GITHUB_TOKEN,
    GITHUB_REPO,
    GITHUB_BRANCH,
    GITHUB_API_BASE,
    generate_sundblom,
    log,
)

MIN_JULIUS_LENGTH = 200  # tecken — kortare = trunkerat


def list_article_files() -> list[dict]:
    """Hämtar alla artikel-JSONar från src/content/articles/ via GitHub API."""
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/src/content/articles"
    resp = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
    resp.raise_for_status()
    return [f for f in resp.json() if f["name"].endswith(".json")]


def get_article(file_info: dict) -> tuple[dict, str]:
    """Hämtar och avkodar en artikel-JSON. Returnerar (data, sha)."""
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = requests.get(file_info["url"], headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    return json.loads(raw), payload["sha"]


def update_article(path: str, data: dict, sha: str) -> None:
    """Pushar uppdaterad artikel-JSON till repot."""
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    content = json.dumps(data, ensure_ascii=False, indent=2)
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": f"🔧 Reparerar julius_text: {data['headline'][:60]}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    resp = requests.put(url, headers=headers, json=payload)
    resp.raise_for_status()
    log.info("✅ Uppdaterad: %s", path)


def main() -> None:
    if not GOOGLE_API_KEY:
        log.error("GOOGLE_API_KEY saknas.")
        sys.exit(1)
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.error("GITHUB_TOKEN eller GITHUB_REPO saknas.")
        sys.exit(1)

    log.info("═══ Backrun-reparation startar ═══")
    files = list_article_files()
    log.info("Hittade %d artikelfiler totalt.", len(files))

    repaired = 0
    skipped = 0

    for file_info in sorted(files, key=lambda f: f["name"]):
        data, sha = get_article(file_info)
        julius = data.get("julius_text", "")

        # Krönikor och reflektioner saknar källartikel — generate_sundblom
        # skulle skriva om dem till ledare utan underlag.
        if data.get("kind") == "kronika" or data.get("content_type") == "reflektion":
            skipped += 1
            continue

        if len(julius) >= MIN_JULIUS_LENGTH:
            skipped += 1
            continue

        log.info("─── Reparerar: %s (%d chars) ───", file_info["name"], len(julius))

        new_julius = generate_sundblom(
            headline=data["headline"],
            source_url=data.get("source_url", ""),
            body=data.get("body", ""),
        )
        data["julius_text"] = new_julius
        update_article(file_info["path"], data, sha)
        repaired += 1

    log.info("Klar: %d reparerade, %d redan ok.", repaired, skipped)
    log.info("═══ Reparation klar. ═══")


if __name__ == "__main__":
    main()
