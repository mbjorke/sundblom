# Åland igår och idag 🗞️

Autonom nattlig generator som hämtar senaste nytt från Ålands Radio och
omvandlar det till en Sundblomsk ledarartikel i 1920-talsstil — ställd bredvid
originalartikeln. Publiceras automatiskt varje dag kl. 16:00 UTC på **[nudå.ax](https://nudå.ax)**.

> *"Låtom oss icke vika från självstyrelsens heliga grundvalar."* — J.S.

---

## Vad det gör

- **Skrapar** topprubrikerna från [alandsradio.ax](https://alandsradio.ax/nyheter)
- **Genererar** Julius Sundbloms AI-tolkning (1920-talsprosa, politisk världssyn)
- **Visar** originalartikeln från Ålands Radio i höger kolumn
- **Publicerar** `index.html` + arkivpost med beskrivande URL via GitHub API
- **Tar en skärmdump** (Playwright) och publicerar som `og-image.png`
- Körs automatiskt via **GitHub Actions** och hostas på **Cloudflare Pages**

---

## Mappstruktur

```
.
├── main.py                 # Huvudskriptet
├── template.html           # HTML-mall (split-screen tidningslayout)
├── requirements.txt        # Python-beroenden (inkl. Playwright)
├── index.html              # ← Genereras automatiskt
├── og-image.png            # ← Genereras automatiskt (Playwright screenshot)
├── robots.txt              # ← Genereras automatiskt
├── arkiv/                  # ← Byggs upp dag för dag
│   ├── index.html          # Arkivöversikt
│   └── 2026-03-19-flicklaget-behandlas-nedlatande.html
└── .github/
    └── workflows/
        └── nightly.yml     # Kör kl. 16:00 UTC varje dag
```

---

## Kom igång

### 1. Klona och konfigurera

```bash
git clone https://github.com/mbjorke/sundblom.git
cd sundblom
pip install -r requirements.txt
playwright install chromium --with-deps
```

### 2. Lägg in API-nyckel som GitHub Secret

**Settings → Secrets and variables → Actions → New repository secret**

| Secret              | Värde                             |
|---------------------|-----------------------------------|
| `ANTHROPIC_API_KEY` | Din nyckel från console.anthropic.com |

> `GITHUB_TOKEN` skapas automatiskt av GitHub Actions.

### 3. Sätt upp Cloudflare Pages

1. **Cloudflare Dashboard → Workers & Pages → Create application → Pages**
2. Koppla GitHub-repot
3. Build command: *(tomt)* · Output directory: `/`
4. Lägg till custom domain under **Custom domains**

### 4. Testa manuellt

```bash
gh workflow run nightly.yml --repo DITT_REPO
```

Eller via **Actions → 🗞️ Sundbloms nattliga kommentar → Run workflow**.

---

## Lokal körning

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_TOKEN="ghp_..."
export GITHUB_REPO="användarnamn/sundblom"
export GITHUB_BRANCH="main"

python main.py
```

---

## Kända lösningar & lärdomar

### IDN-domän (å/ä/ö) + Facebook OG-bild

**Problem:** Facebook kunde inte provisionera HTTPS-certifikat för `nudå.ax` via
GitHub Pages, och OG-taggar fungerade inte med IDN-URL:en.

**Lösning:**
1. **Cloudflare Pages** istället för GitHub Pages — Cloudflare hanterar IDN
   nativt eftersom SSL termineras internt.
2. **`og:image`** pekar på `sundblom.pages.dev/og-image.png` (ASCII-URL) för
   garanterad kompatibilitet med alla sociala plattformar.
3. **`og:url`** pekar på `sundblom.pages.dev` för Facebook-delning.
4. Custom domain i Cloudflare Pages anges som punycode: `xn--nud-wla.ax`
   (nudå.ax = xn--nud-wla.ax).

**Verifiering:** [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/)
med `https://xn--nud-wla.ax`.

---

### fbclid-parametern i URL:en

Facebook lägger automatiskt till `?fbclid=...` när någon klickar en länk.
Städas bort klient-sidan utan omladdning:

```javascript
if (window.location.search) {
  const p = new URLSearchParams(window.location.search);
  ['fbclid','utm_source','utm_medium','utm_campaign'].forEach(k => p.delete(k));
  history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
}
```

---

## Felhantering

| Situation                        | Beteende                                                    |
|----------------------------------|-------------------------------------------------------------|
| Ålands Radio är nere             | Fallback: "den öronbedövande tystnaden från fastlandet"     |
| Ingen rubrik hittad              | Samma fallback                                              |
| `ANTHROPIC_API_KEY` saknas       | Avslutas med tydligt felmeddelande                          |
| Playwright-screenshot misslyckas | Varning loggas, körningen fortsätter utan OG-bild           |

---

## Schema

```yaml
- cron: "0 16 * * *"   # 16:00 UTC = 18:00 finsk vintertid / 19:00 sommartid
```

[Cron-syntax →](https://crontab.guru/)
