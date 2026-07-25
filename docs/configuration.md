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
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | leer | Optionale OAuth2-Client-Credentials für ein höheres OpenSky-Rate-Limit. Siehe unten und [Datenquellen](data-sources.md). |
| `TZ` | (System-Default) | Zeitzone für Anzeige der Landezeiten. Sollte auf `Europe/Berlin` stehen, sonst weichen angezeigte Uhrzeiten vom tatsächlichen Landezeitpunkt ab. |

## Poll-Intervall

Wie oft die aktivierten Datenquellen pro beobachtetem Flughafen abgefragt
werden, ist global für alle Accounts gemeinsam (die Erkennungslogik selbst
ist geteilt) – nicht über eine Umgebungsvariable, sondern über die
Einstellungen-Seite (Standard: 90 Sekunden, Minimum: 30 Sekunden).

## Datenquellen und Zugangsdaten

Welche Datenquellen (OpenSky, adsb.lol, ...) aktiv sind, wird unter
**Einstellungen → Datenquellen** gepflegt, siehe [Datenquellen](data-sources.md)
für Details zu den einzelnen Quellen.

`OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` lassen sich **entweder** per
Umgebungsvariable **oder** direkt in den Einstellungen setzen:

- Ist die Umgebungsvariable gesetzt, gewinnt sie immer - das entsprechende
  Feld in den Einstellungen wird dann als gesperrt/nicht editierbar
  angezeigt (ein Deploy-Secret soll sich nicht versehentlich über die
  Weboberfläche überschreiben lassen).
- Ist sie **nicht** gesetzt, lassen sich Client-ID/-Secret direkt in den
  Einstellungen eintragen und landen dort in der Datenbank.

adsb.lol braucht keine Zugangsdaten, dafür gibt es in den Einstellungen kein
Credential-Feld - nur den Aktiviert-Schalter.

## Watchlist

Die eingebauten Kategorien und die eigene Watchlist werden komplett über die
Weboberfläche gepflegt – siehe [Watchlist](watchlist.md).

## Benachrichtigungskanäle

Pushover, ntfy, Telegram, Discord, generischer Webhook und E-Mail werden
komplett über die Einstellungen-Seite pro Account konfiguriert, nicht über
Umgebungsvariablen – siehe [Start](index.md#features).
