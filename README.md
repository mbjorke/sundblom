# Åland igår och idag 🗞️

Autonom daglig generator som hämtar senaste nytt från Ålands Radio och
omvandlar det till en Sundblomsk ledarartikel i 1920-talsstil — ställd bredvid
originalartikeln. Publiceras automatiskt varje dag kl. 16:00 UTC på **[nudå.ax](https://nudå.ax)**.

> *"Låtom oss icke vika från självstyrelsens heliga grundvalar."* — J.S.

---

## Inspirerat av

Ett stort tack till **Jimmy Flink** och hans [klassiskanyheter.se](https://klassiskanyheter.se/) — ett pionjärprojekt som visade att klassisk tidningsstil och modern webbteknik kan mötas på ett genuint och stilfullt sätt. Hans arbete var en direkt inspiration till det här projektet.

---

## Vad det gör

- **Skrapar** upp till 20 topprubrikerna från [alandsradio.ax](https://alandsradio.ax/nyheter) i DOM-ordning
- **Genererar** Julius Sundbloms AI-tolkning (1920-talsprosa, politisk världssyn) via Claude API
- **Visar** originalartikeln från Ålands Radio i höger kolumn
- **Sparar** artikel-data som JSON i `src/content/articles/`
- **Bygger** statisk HTML via Astro + Bun vid varje push (Cloudflare Pages)
- Körs automatiskt via **GitHub Actions** kl. 16:00 UTC

---

## Arkitektur

```
Python (main.py)
  → Scrapa alandsradio.ax (upp till 20 artiklar, DOM-ordning)
  → Filtrera bort redan processade (seen-urls.json)
  → Redaktörsomdömet (selector.py) → förtjänar nyheten en ledare alls?
  → Anropa Claude API → Julius Sundbloms tolkning
  → Pusha src/content/articles/YYYY-MM-DD-slug.json till GitHub
  → Gav dagen ingen ledare? → veckokrönika (kronika.py)

Cloudflare Pages (vid varje push)
  → bun install && bun run build
  → Astro läser JSON-filerna → genererar statisk HTML
  → Deployar dist/ till nudå.ax
```

---

## Mappstruktur

```
.
├── main.py                          # Scraping + Claude API + JSON-push
├── selector.py                      # Redaktörsomdömet — grind före generering
├── kronika.py                       # Veckokrönika på dagar utan värdig nyhet
├── backfill.py                      # Manuell backfill av historiska artiklar
├── requirements.txt                 # Python-beroenden
├── package.json                     # Astro + Bun
├── astro.config.mjs                 # Astro-konfiguration (static, prefetch)
├── src/
│   ├── content/
│   │   ├── config.ts                # Zod-schema för artikeldata
│   │   └── articles/                # ← JSON-filer genereras hit av main.py
│   │       └── 2026-03-19-zekaj-cosmic-...json
│   ├── layouts/
│   │   └── BaseLayout.astro         # HTML-skal, OG-taggar, View Transitions
│   ├── components/
│   │   ├── Masthead.astro           # Tidningsmasthead
│   │   └── ArticleSplit.astro       # Tvåkolumnslayout + tapnavigering
│   └── pages/
│       ├── index.astro              # Senaste artikeln
│       ├── arkiv/
│       │   ├── index.astro          # Arkivöversikt
│       │   └── [slug].astro         # Enskild artikel (statisk routing)
│       └── tillganglighet/
│           └── index.astro          # Tillgänglighetspolicy
├── public/
│   ├── og-image.png                 # ← Genereras av main.py (Playwright)
│   └── robots.txt                   # Välkomnar sökmotorer och AI-botar
└── .github/
    └── workflows/
        ├── daily.yml                # Kör kl. 16:00 UTC varje dag
        └── backfill.yml             # Manuell backfill-trigger
```

---

## Redaktörsomdömet och veckokrönikan

### Grinden (`selector.py`)

Varje nyhet bedöms av en billig modell innan Julius får skriva: nyhetsvärde
1–10, tematisk passning och substans. Under `SELECTOR_MIN_VALUE` (default 6)
blir det ingen ledare. Grinden är avsiktligt sträng — Julius var redaktör och
valde bort. Ungefär 12 % av nyheterna passerar.

### Krönikan (`kronika.py`)

Baksidan av en sträng grind: på helger och stilla dagar avvisas *allt*, och då
publicerades tidigare ingenting alls — över hälften av dagarna sedan mars var
sådana tomma dagar. I stället för att sänka ribban för ledaren skriver Julius
då en **veckokrönika**: en betraktelse över veckan som gått, byggd på veckans
publicerade ledare och de notiser som avvisades.

Krönikan skrivs när **alla** villkor är uppfyllda:

| Villkor | Miljövariabel | Default |
|---|---|---|
| Krönikan är påslagen | `KRONIKA_ENABLED` | `1` |
| Körningen sker sent på dagen (dagens sista körning, 22:00 EET) | `KRONIKA_AFTER_UTC_HOUR` | `18` |
| Ingen utgåva är publicerad detta datum | — | — |
| Minst så här många dagar sedan förra krönikan (en helg ger alltså en, inte två) | `KRONIKA_MIN_DAYS` | `3` |
| Veckan bjöd på minst så här många rubriker | `KRONIKA_MIN_RUBRIKER` | `5` |
| Så många dagar bakåt krönikan blickar | `KRONIKA_DAGAR` | `7` |

Tidpunkten är vald så att söndagens veckobetraktelse (`reflect.py`, 17:00 UTC)
hinner före: publicerar den en betraktelse blir dagen inte längre tom, och
krönikan uteblir. I praktiken ger det krönika på lördagar och betraktelse på
söndagar.

Krönikan sparas som vanlig artikel-JSON med `"kind": "kronika"` och ett
`sources`-fält. På sajten visar högerkolumnen då **veckans rubriker** med
länkar till Ålands Radio i stället för en enskild originalartikel, och arkivet
märker posten med *Veckokrönika*.

### Deploy-triggern

Cloudflare Pages lyssnar på branchen `deploy`, inte `main`. Endast
`save_last_headline()` flyttar den — pushar av artikel-JSON gör det inte. Både
krönikan och veckobetraktelsen anropar den därför efter att ha sparat sin text;
annars blir texten liggande i repot utan att synas på sajten.

Torrkörning utan API-nyckel — visar beslut, underlag och prompt:

```bash
python kronika.py --selftest             # för idag
python kronika.py --selftest --datum 2026-09-06
python selector.py --selftest
```

---

## Kom igång

### 1. Klona och installera

```bash
git clone https://github.com/mbjorke/sundblom.git
cd sundblom

# Python-beroenden
pip install -r requirements.txt

# Astro/Bun
bun install
```

### 2. Lägg in API-nyckel som GitHub Secret

**Settings → Secrets and variables → Actions → New repository secret**

| Secret              | Värde                                 |
|---------------------|---------------------------------------|
| `ANTHROPIC_API_KEY` | Din nyckel från console.anthropic.com |

> `GITHUB_TOKEN` skapas automatiskt av GitHub Actions.

### 3. Sätt upp Cloudflare Pages

1. **Cloudflare Dashboard → Workers & Pages → Create application → Pages**
2. Koppla GitHub-repot, välj branch (`main` eller `feature/astro-rebuild`)
3. **Build command:** `bun run build`
4. **Output directory:** `dist`
5. Lägg till custom domain under **Custom domains**
   - Ange som punycode: `xn--nud-wla.ax` (= nudå.ax)

### 4. Testa lokalt

```bash
bun run dev        # Startar dev-server på http://localhost:4321
bun run build      # Bygger statisk HTML till dist/
```

### 5. Trigga manuellt

```bash
gh workflow run daily.yml --repo mbjorke/sundblom
```

---

## Lokal Python-körning

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_TOKEN="ghp_..."
export GITHUB_REPO="mbjorke/sundblom"
export GITHUB_BRANCH="main"

python main.py
```

---

## Navigation

- **Desktop:** Hovra över vänster kolumn (Julius) = äldre artikel, höger (Ålands Radio) = nyare
- **Mobil:** Tap på vänster halva av skärmen = äldre, höger halva = nyare (Kindle-stil)
- **Alltid synlig:** `← Äldre / Nyare →` i navbaren längst ned
- **View Transitions:** Astros inbyggda sidövergångar — ingen blinkande vid navigering
- **Prefetch:** Grannsidor prefetchar vid hover/viewport → navigation känns omedelbar

---

## Kända lösningar & lärdomar

### IDN-domän (å/ä/ö) + Facebook OG-bild

**Problem:** Facebook kunde inte provisionera HTTPS-certifikat för `nudå.ax` via
GitHub Pages, och OG-taggar fungerade inte med IDN-URL:en.

**Lösning:**
1. **Cloudflare Pages** istället för GitHub Pages — Cloudflare hanterar IDN nativt
2. **`og:image`** pekar på `sundblom.pages.dev/og-image.png` (ASCII-URL)
3. Custom domain i Cloudflare anges som punycode: `xn--nud-wla.ax`

### Varför Astro + Bun?

Den gamla arkitekturen renderade HTML via Python-strängersättning (`template.html`). Det fungerade men gav:
- Blinkande sidbyten (full page reload)
- Manifest.json för navigering (client-side JS)
- Svårt att underhålla layouten

Astro löser detta med:
- **View Transitions** — sömlösa sidövergångar utan SPA-komplexitet
- **Content Collections** — typsäker JSON-hantering
- **Statisk routing** — `getStaticPaths()` ger prev/next utan manifest.json
- **Prefetch** — grannsidor laddas i förväg

---

## Felhantering

| Situation                        | Beteende                                                    |
|----------------------------------|-------------------------------------------------------------|
| Ålands Radio är nere             | Fallback: "den öronbedövande tystnaden från fastlandet"     |
| Inga nya artiklar idag           | Loggar info, gör inget (seen-urls.json filtrerar)           |
| `ANTHROPIC_API_KEY` saknas       | Avslutas med tydligt felmeddelande                          |
| Playwright-screenshot misslyckas | Varning loggas, körningen fortsätter utan OG-bild           |

---

## Schema

```yaml
- cron: "0 16 * * *"   # 16:00 UTC = 18:00 finsk vintertid / 19:00 sommartid
```

[Cron-syntax →](https://crontab.guru/)
