# Konfiguration

Alle Einstellungen, die vor dem Start feststehen müssen, laufen über
Umgebungsvariablen. Alles, was sich zur Laufzeit pro Account ändert
(Benachrichtigungskanäle, Flughäfen, Watchlist), läuft über die
Weboberfläche selbst.

| Variable | Default | Beschreibung |
|---|---|---|
| `RAREBIRDALERT_DB_PATH` | `/app/data/rarebirdalert.db` | Pfad zur SQLite-Datenbank. |
| `RAREBIRDALERT_AIRCRAFT_DB_CACHE` | `/app/data/aircraft-db.csv` | Pfad zum lokalen Cache der OpenSky-Flugzeugdatenbank. |
| `SESSION_SECRET_KEY` | zufällig bei jedem Start | Signiert die Session-Cookies. Ohne festen Wert sind nach jedem Neustart alle abgemeldet. Generieren mit `python3 -c "import secrets; print(secrets.token_hex(32))"`. |
| `REGISTRATION_ENABLED` | `true` | Auf `false` setzen, sobald alle gewünschten Accounts existieren, um `/register` zu sperren. Bestehende Accounts können sich weiterhin einloggen. |
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | leer | Optionale OAuth2-Client-Credentials für ein höheres OpenSky-Rate-Limit. Siehe [Datenquelle](data-sources.md). |
| `TZ` | (System-Default) | Zeitzone für Anzeige der Landezeiten. Sollte auf `Europe/Berlin` stehen, sonst weichen angezeigte Uhrzeiten vom tatsächlichen Landezeitpunkt ab. |

## Poll-Intervall

Wie oft OpenSky pro beobachtetem Flughafen abgefragt wird, ist global für
alle Accounts gemeinsam (die Erkennungslogik selbst ist geteilt) – nicht über
eine Umgebungsvariable, sondern über die Einstellungen-Seite (Standard: 90
Sekunden, Minimum: 30 Sekunden).

## Watchlist

Die eingebauten Kategorien und die eigene Watchlist werden komplett über die
Weboberfläche gepflegt – siehe [Watchlist](watchlist.md).

## Benachrichtigungskanäle

Pushover, ntfy, Telegram, Discord, generischer Webhook und E-Mail werden
komplett über die Einstellungen-Seite pro Account konfiguriert, nicht über
Umgebungsvariablen – siehe [Start](index.md#features).
