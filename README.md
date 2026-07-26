# RareBirdAlert

![CI](https://github.com/steven-streller/RareBirdAlert/actions/workflows/ci.yml/badge.svg)
![Docker Publish](https://github.com/steven-streller/RareBirdAlert/actions/workflows/docker-publish.yml/badge.svg)
![Docs](https://github.com/steven-streller/RareBirdAlert/actions/workflows/docs.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)

Beobachtet frei wählbare Flughäfen über [OpenSky Network](https://opensky-network.org/)
und schickt eine Benachrichtigung, sobald dort ein "besonderes" Flugzeug
landet – Militärmaschinen, der Airbus Beluga, eine Antonov, eine alte DC-3
oder was auch immer du auf deine eigene Watchlist setzt. Mehrbenutzerfähig:
jeder Account hat seine eigenen Flughäfen, seine eigene Watchlist und seine
eigenen Benachrichtigungskanäle (Pushover, ntfy, Telegram, Discord, Webhook,
E-Mail).

**Docs: <https://steven-streller.github.io/RareBirdAlert/>**

Dort steht alles Weitere: Setup-Anleitungen für Entwicklung, Linux (systemd),
Docker, Podman und Kubernetes, die vollständige Feature-Übersicht, die
Watchlist-Syntax sowie die Referenz aller Umgebungsvariablen.

## Schnellstart

```bash
docker compose up -d --build
```

Danach unter <http://localhost:8000> registrieren und loslegen.

## Sicherheit

Schwachstellen bitte nicht über öffentliche Issues melden, siehe
[SECURITY.md](SECURITY.md).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
