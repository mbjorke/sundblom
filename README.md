# Sundbloms Radio-kommentarer 🗞️

Autonom nattlig generator som hämtar senaste nytt från Ålands Radio och
omvandlar det till en Sundblomsk ledarartikel i 1920-talsstil — publicerad
automatiskt på GitHub Pages.

---

## Mappstruktur

```
.
├── main.py                          # Huvudskriptet
├── template.html                    # HTML-mall (tidningsstil 1920-tal)
├── requirements.txt                 # Python-beroenden
├── index.html                       # ← Genereras automatiskt (GitHub Pages)
└── .github/
    └── workflows/
        └── nightly.yml              # Kör varje natt kl. 03:00 UTC
```

---

## Snabbstart

### 1. Skapa ett nytt GitHub-repo

```bash
git init sundblom-kommentarer
cd sundblom-kommentarer
# Kopiera in filerna härifrån
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/DITT_ANVÄNDARNAMN/sundblom-kommentarer.git
git push -u origin main
```

### 2. Aktivera GitHub Pages

Gå till repots **Settings → Pages**:
- **Source:** `Deploy from a branch`
- **Branch:** `main` / `/ (root)`
- Klicka **Save**

Din sida är tillgänglig på:
`https://DITT_ANVÄNDARNAMN.github.io/sundblom-kommentarer/`

### 3. Lägg in API-nycklar som GitHub Secrets

Gå till **Settings → Secrets and variables → Actions → New repository secret**:

| Secret-namn        | Värde                                      |
|--------------------|-------------------------------------------|
| `ANTHROPIC_API_KEY`| Din nyckel från console.anthropic.com      |

> **OBS:** `GITHUB_TOKEN` skapas *automatiskt* av GitHub Actions — du behöver inte lägga in den manuellt.

### 4. Testa manuellt

Gå till **Actions → 🗞️ Sundbloms nattliga kommentar → Run workflow**.

---

## Miljövariabler (för lokal körning)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_TOKEN="ghp_..."
export GITHUB_REPO="ditt-namn/sundblom-kommentarer"
export GITHUB_BRANCH="main"

pip install -r requirements.txt
python main.py
```

---

## Felhantering

| Situation                          | Beteende                                               |
|------------------------------------|--------------------------------------------------------|
| Ålands Radio är nere               | Faller tillbaka på "den öronbedövande tystnaden från fastlandet" |
| Inga rubriker hittade på sidan     | Samma fallback som ovan                                |
| `ANTHROPIC_API_KEY` saknas         | Programmet avslutas med tydligt felmeddelande          |
| `GITHUB_TOKEN`/`GITHUB_REPO` saknas| Programmet avslutas med tydligt felmeddelande         |
| HTTP-fel vid GitHub-push           | `requests.HTTPError` kastas med statuskod              |

---

## Anpassa schemat

I `.github/workflows/nightly.yml`, ändra cron-uttrycket:

```yaml
- cron: "0 3 * * *"   # 03:00 UTC = 05:00 finsk sommartid
```

[Cron-syntax-referens →](https://crontab.guru/)

---

*"Låtom oss icke vika från självstyrelsens heliga grundvalar."*
— J.S.
