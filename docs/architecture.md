# Architektur

Ein Überblick für alle, die am Code mitarbeiten oder einfach verstehen
wollen, wie die Teile zusammenspielen – für den Betrieb selbst ist das nicht
nötig, siehe stattdessen die [Setup-Anleitungen](setup/development.md).

## Stack

FastAPI-App, serverseitig gerendertes HTML (Jinja2) mit HTMX für
Interaktivität ohne eigenes JavaScript-Build, SQLite über SQLModel
(SQLAlchemy, WAL-Modus), APScheduler für Hintergrund-Jobs im selben Prozess –
ein einziger Container, kein separater Worker/Broker.

## Datenmodell

| Tabelle | Umfang | Zweck |
|---|---|---|
| `Airport` | global | ICAO-/IATA-Code, Name, Koordinaten – aus dem gebündelten Verzeichnis übernommen |
| `AirportWatch` | pro Nutzer | Verknüpfung User↔Airport, inkl. individuellem Suchradius |
| `AircraftCategory` | global, geseedet | Eingebaute Kategorien (Militär, Eurofighter, ...) |
| `WatchlistEntry` | pro Nutzer | Eigene Einträge (Typcode/Kennung/ICAO24/Callsign-Präfix/Betreiber) |
| `AircraftTrackState` | global | Letzter bekannter Boden-/Luft-Status je (Flugzeug, Flughafen) - Grundlage der Landeerkennung |
| `AircraftMetadata` | global, Cache | Lokale Kopie der OpenSky-Flugzeugdatenbank (Typ, Kennung, Betreiber je ICAO24) |
| `Sighting` | global | Eine erkannte Landung eines "besonderen" Flugzeugs |
| `SightingMatch` | global | Warum eine Sighting als besonders galt (Kategorie oder Watchlist-Eintrag) |
| `User` | pro Account | E-Mail, bcrypt-Passworthash |
| `Setting` | global | Aktuell nur `poll_interval_seconds` |
| `UserSetting` | pro Nutzer | Benachrichtigungskanäle + Kategorie-Toggles |
| `NotificationLog` | pro Nutzer | Verhindert doppelte Benachrichtigungen für dieselbe Sichtung |

Warum sind Flughäfen/Sichtungen global, aber Watches/Watchlist/Kanäle pro
Nutzer? Die Erkennung selbst (welches Flugzeug ist wo gelandet) ist für alle
identisch – das einmal pro Flughafen bei OpenSky abzufragen und zu teilen
spart unnötige API-Aufrufe. Wer benachrichtigt werden will und worüber ist
dagegen inhärent persönlich.

## Erkennung (`app/opensky.py`, `app/aircraft_db.py`, `app/matcher.py`, `app/scheduler.py`)

`poll_job` läuft alle `poll_interval_seconds` (Standard 90, global
einstellbar). Für jeden eindeutig beobachteten Flughafen (mit dem größten von
allen Watchern angeforderten Radius) liefert `opensky.fetch_states` die
aktuellen ADS-B-Zustände im Umkreis. Für jedes Flugzeug prüft
`scheduler._process_state`:

1. **Landeerkennung**: `AircraftTrackState` je (icao24, Flughafen) merkt sich
   den letzten `on_ground`-Status. Ein Wechsel von `False` auf `True` gilt
   als Landung; bleibt das Flugzeug am Boden, löst der nächste Poll nichts
   erneut aus. Nach 6 Stunden ohne Sichtung wird der Zustand vergessen, damit
   eine spätere Rückkehr wieder als neue Landung zählt.
2. **Typ-Anreicherung**: `aircraft_db.lookup(icao24)` liefert Typ, Kennung
   und Betreiber aus dem lokalen Cache der OpenSky-Flugzeugdatenbank.
3. **Matching**: `matcher.matches(...)` prüft die angereicherten Daten gegen
   alle aktiven `AircraftCategory`- und `WatchlistEntry`-Muster (über alle
   Nutzer hinweg). Kein Treffer → verworfen, kein `Sighting`-Datensatz.
   Treffer → `Sighting` + `SightingMatch`-Zeile(n) angelegt.

`notify_check_job` läuft alle 30 Sekunden und iteriert über die letzten 200
Sichtungen und alle Nutzer: benachrichtigt wird, wer den betroffenen
Flughafen beobachtet **und** mindestens einen der Treffer bei sich aktiviert
hat (eingebaute Kategorie oder eigener Watchlist-Eintrag), sofern noch keine
`NotificationLog`-Zeile für (Nutzer, Sichtung) existiert.

Ein separater, wöchentlicher Job (`aircraft_db.refresh_aircraft_db`) hält den
lokalen Flugzeugdatenbank-Cache aktuell.

## Auth (`app/auth.py`, `app/security.py`)

Cookie-Session über Starlettes `SessionMiddleware` (mit `itsdangerous`
signiert, `SESSION_SECRET_KEY`), Passwörter mit `bcrypt` gehasht. Die
`require_user`-Dependency gated alle Routen außer `/login`, `/register` und
`/healthz` – bei fehlender Session ein 303-Redirect zu `/login` (inkl.
`HX-Redirect`-Header, damit HTMX-Requests nicht nur den betroffenen
Seitenausschnitt austauschen, sondern die ganze Seite neu laden).

## Benachrichtigungskanäle (`app/notifications.py`)

Kanäle sind als Daten definiert (`CHANNELS`-Dict: Label, Sendefunktion,
Formularfelder) statt als Code pro Kanal in den Templates. Die
Einstellungen-Seite rendert die Formulare generisch aus dieser Struktur – ein
neuer Kanal braucht dort nur einen neuen Eintrag, keine Template-Änderung.

## SQLite unter Nebenläufigkeit

Der wöchentliche Import der ~500.000-Zeilen-Flugzeugdatenbank läuft in der
gleichen SQLite-Datei wie die Web-Anfragen. Damit das keine `database is
locked`-Fehler auslöst, läuft die Datenbank im WAL-Modus (Reader blockieren
Writer nicht und umgekehrt) und der Import committet alle 5000 Zeilen statt
in einer einzigen langen Transaktion – siehe `_set_sqlite_pragma` in
`app/db.py` und `_load_into_db` in `app/aircraft_db.py`.
