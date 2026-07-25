# Entwicklung

```bash
git clone https://github.com/steven-streller/RareBirdAlert.git
cd RareBirdAlert
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Tests & Lint

```bash
pytest              # siehe tests/ für die aktuelle Anzahl
ruff check .         # Lint
```

CI (`.github/workflows/ci.yml`) führt beides plus einen Docker-Build-Check bei
jedem Push/PR aus.

## Lokal starten

```bash
RAREBIRDALERT_DB_PATH=./data/rarebirdalert.db SESSION_SECRET_KEY=dev-only \
  uvicorn app.main:app --reload --port 8000
```

Die SQLite-Datenbank landet dann in `./data/rarebirdalert.db` (per
`.gitignore` ausgeschlossen). `--reload` startet den Server bei
Codeänderungen neu – Achtung: das reißt den laufenden APScheduler mit hoch
und den ersten Poll/Metadatenbank-Download erneut an, das ist für lokale
Entwicklung unproblematisch.

## Projektstruktur

```text
app/
  main.py            FastAPI-Routen (Auth, Dashboard, Flughäfen, Watchlist, Settings)
  auth.py            Sessions, Passwort-Hashing-Dependency
  security.py        bcrypt hash/verify
  db.py              Engine, Schema-Erstellung, Settings-Helper, Airport-Verzeichnis
  models.py          SQLModel-Tabellen
  opensky.py         OpenSky-REST-Client (Bounding-Box-Abfrage, OAuth2)
  aircraft_db.py      Download/Cache der OpenSky-Flugzeugdatenbank
  matcher.py          Reine Kategorie-/Watchlist-Matching-Logik
  scheduler.py        APScheduler-Jobs (Poll, Benachrichtigungs-Check, DB-Refresh)
  notifications.py   Kanal-Registry + Sendefunktionen (Pushover/ntfy/...)
  data/airports.csv   Gebündeltes Flughafen-Verzeichnis (OurAirports, public domain)
  templates/         Jinja2 + HTMX
  static/            CSS, htmx.min.js
tests/               pytest, inkl. TestClient-basierte HTTP-Tests
docs/                diese Doku (MkDocs Material)
```

## Neuen Benachrichtigungskanal hinzufügen

Kanäle sind in `app/notifications.py` als Daten definiert (`CHANNELS`-Dict:
Label, Sendefunktion, Felder). Ein neuer Kanal braucht dort nur einen neuen
Eintrag – die Einstellungen-Seite rendert Formularfelder automatisch aus den
`fields`-Angaben, keine Template-Änderung nötig.
