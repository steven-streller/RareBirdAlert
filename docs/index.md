# RareBirdAlert

Beobachtet frei wählbare Flughäfen über [OpenSky Network](https://opensky-network.org/)
und schickt eine Benachrichtigung, sobald dort ein "besonderes" Flugzeug
landet – Militärmaschinen, der Airbus Beluga, eine Antonov, eine alte DC-3
oder was auch immer du auf deine eigene Watchlist setzt. Mehrbenutzerfähig:
jeder Account hat seine eigenen Flughäfen, seine eigene Watchlist und seine
eigenen Benachrichtigungskanäle.

Wähle links eine Setup-Variante, um loszulegen:

- **[Entwicklung](setup/development.md)** – lokal am Code arbeiten
- **[Linux](setup/linux.md)** – direkt auf einem Server als systemd-Dienst
- **[Docker](setup/docker.md)** – `docker compose up -d --build`
- **[Podman](setup/podman.md)** – rootless, inkl. Quadlet-Unit
- **[Kubernetes](setup/kubernetes.md)** – Beispiel-Manifest für k3s & Co.

Danach [Konfiguration](configuration.md) für die Referenz aller Umgebungsvariablen.

## Features

- **Dashboard** – nach Tag gruppierter Sichtungs-Feed über alle deine
  beobachteten Flughäfen
- **Flughäfen** – per ICAO-/IATA-Code oder Namen suchen (gebündeltes
  Verzeichnis, kein externer Geocoding-Aufruf nötig) und mit eigenem
  Suchradius hinzufügen
- **Watchlist** – eine eingebaute, editierbare Startliste an Kategorien
  (Militär, Eurofighter Typhoon, Spezial-Transporter, historische Klassiker)
  plus beliebig viele eigene Einträge nach Flugzeugtyp, Kennung,
  ICAO24-Hexcode, Callsign-Präfix oder Betreiber – siehe [Watchlist](watchlist.md)
- **Einstellungen** – die Benachrichtigungskanäle sind pro Account:
    - **Pushover** (User Key + API Token von https://pushover.net)
    - **ntfy** (Server-URL + Topic, z.B. der öffentliche https://ntfy.sh oder
      eine eigene Instanz)
    - **Telegram** (Bot-Token + Chat-ID)
    - **Discord** (Webhook-URL eines Kanals)
    - **Generischer Webhook** (POST von `{title, message, url}` als JSON an
      eine beliebige URL)
    - **E-Mail** (SMTP-Zugangsdaten)

  Jeder Kanal hat einen eigenen "Testen"-Button, der mit dem zuletzt
  gespeicherten Stand dieses Kanals eine Testbenachrichtigung schickt. Eine
  Schritt-für-Schritt-Anleitung pro Kanal gibt's unter
  [Benachrichtigungskanäle](notifications.md).
- **Admin** – nur für den Admin-Account sichtbar: Poll-Intervall (global, ein
  API-Aufruf pro aktivierter Datenquelle, beobachtetem Flughafen und Zyklus)
  und welche Datenquellen aktiv sind, siehe [Konfiguration](configuration.md#admin-account).

Registrierung ist standardmäßig offen – jeder mit Zugriff auf die URL kann
sich einen Account anlegen. Der **erste** registrierte Account wird
automatisch zum Admin, alle weiteren sind normale Nutzer. Sobald alle
gewünschten Accounts existieren, [`REGISTRATION_ENABLED=false`](configuration.md)
setzen, um weitere Registrierungen zu blockieren – bestehende Accounts können
sich weiterhin einloggen.

Beim ersten Start wird sofort einmal die Flugzeug-Metadatenbank geladen (kann
bis zu einer Minute dauern) und der erste Poll-Zyklus angestoßen; danach läuft
alles automatisch im eingestellten Intervall.

## Mehr

- **[Datenquelle (OpenSky)](data-sources.md)** – Rate-Limits, optionale
  OAuth2-Zugangsdaten, Grenzen der kostenlosen Nutzung
- **[FAQ / Troubleshooting](faq.md)** – häufige Stolpersteine
- **[Architektur](architecture.md)** – wie Polling, Erkennung und
  Multi-Tenancy zusammenspielen
- **[Mitwirken](contributing.md)** – Branch/PR-Ablauf, Tests, Lint
- **[Sicherheitslücke melden](https://github.com/steven-streller/RareBirdAlert/security/policy)**
  – Meldeweg für Schwachstellen (siehe `SECURITY.md` im Repo)
