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
| `METRICS_TOKEN` | leer (offen) | Optionales Bearer-Token, um `/metrics` abzusichern. Siehe [Monitoring](monitoring.md). |
| `RAREBIRDALERT_BACKUP_DIR` | `/app/data/backups` | Zielverzeichnis für automatische Datenbank-Backups. Siehe unten. |
| `RAREBIRDALERT_BACKUP_KEEP` | `7` | Anzahl der aufzubewahrenden Backups (älteste werden nach jedem neuen Backup gelöscht). `0` deaktiviert die Rotation - dann bleiben alle Backups erhalten. |
| `LOG_FORMAT` | `text` | Auf `json` setzen für strukturiertes JSON-Logging (auch für Uvicorn/APScheduler). Siehe [Monitoring](monitoring.md). |

## Admin-Account

Der erste Account, der sich registriert, wird automatisch zum Admin - alle
danach registrierten Accounts sind normale Nutzer. Nur der Admin-Account
sieht den **Admin**-Menüpunkt und kann Poll-Intervall und Datenquellen
ändern (siehe unten); normale Accounts verwalten dort weiterhin ihre eigenen
Flughäfen, ihre Watchlist und ihre Benachrichtigungskanäle. Es gibt aktuell
keine Möglichkeit, den Admin-Status über die Weboberfläche zu übertragen.

Bei einem Upgrade einer bestehenden Instanz (die `is_admin` noch nicht
kannte) wird beim nächsten Start automatisch der am längsten bestehende
Account zum Admin gemacht, damit niemand ausgesperrt bleibt.

## Login-Schutz

`/login` ist gegen Brute-Force-Versuche abgesichert: nach 5 falschen
Passwörtern für dieselbe Kombination aus Client-IP und E-Mail-Adresse
innerhalb von 5 Minuten wird weiter versucht abgelehnt ("Zu viele
Fehlversuche"), unabhängig davon, ob das Passwort korrekt ist. Ein
erfolgreicher Login setzt den Zähler zurück. Das ist ein rein
prozessinterner Zähler (kein Redis nötig) und setzt sich bei jedem
Container-Neustart zurück - für die Ein-Container-Architektur dieses
Projekts ausreichend.

## Poll-Intervall

Wie oft die aktivierten Datenquellen pro beobachtetem Flughafen abgefragt
werden, ist global für alle Accounts gemeinsam (die Erkennungslogik selbst
ist geteilt) – nicht über eine Umgebungsvariable, sondern über die
**Admin**-Seite (Standard: 90 Sekunden, Minimum: 30 Sekunden).

## Backups

RareBirdAlert sichert seine SQLite-Datenbank automatisch (Standard: alle 24
Stunden, unter **Admin → Allgemein** einstellbar) über SQLites eingebautes
`VACUUM INTO` - ein konsistenter Snapshot, der auch während laufendem
Betrieb erstellt werden kann, ohne den Poll-Zyklus zu pausieren. Ältere
Backups werden automatisch rotiert (`RAREBIRDALERT_BACKUP_KEEP`, Standard:
die letzten 7). Auf der **Admin**-Seite lässt sich außerdem jederzeit
manuell ein Backup anstoßen und jedes vorhandene Backup herunterladen.

**Wiederherstellen:** Container stoppen, die heruntergeladene
Backup-Datei über den Pfad aus `RAREBIRDALERT_DB_PATH` kopieren, Container
neu starten.

## Datenquellen und Zugangsdaten

Welche Datenquellen (OpenSky, adsb.lol, ...) aktiv sind, wird unter
**Admin → Datenquellen** gepflegt (nur für den Admin-Account sichtbar),
siehe [Datenquellen](data-sources.md) für Details zu den einzelnen Quellen.

`OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` lassen sich **entweder** per
Umgebungsvariable **oder** direkt auf der Admin-Seite setzen:

- Ist die Umgebungsvariable gesetzt, gewinnt sie immer - das entsprechende
  Feld wird dann als gesperrt/nicht editierbar angezeigt (ein Deploy-Secret
  soll sich nicht versehentlich über die Weboberfläche überschreiben lassen).
- Ist sie **nicht** gesetzt, lassen sich Client-ID/-Secret direkt auf der
  Admin-Seite eintragen und landen dort in der Datenbank.

adsb.lol braucht keine Zugangsdaten, dafür gibt es dort kein Credential-Feld
- nur den Aktiviert-Schalter.

## Watchlist

Die eingebauten Kategorien und die eigene Watchlist werden komplett über die
Weboberfläche gepflegt – siehe [Watchlist](watchlist.md).

## Benachrichtigungskanäle

Pushover, ntfy, Telegram, Discord, generischer Webhook und E-Mail werden
komplett über die Einstellungen-Seite pro Account konfiguriert, nicht über
Umgebungsvariablen – siehe [Start](index.md#features).
