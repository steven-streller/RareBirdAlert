# Linux (systemd)

Für den Betrieb direkt auf einem Server, ohne Container – z.B. auf einem
Raspberry Pi oder einem kleinen VPS.

## Installation

```bash
sudo useradd --system --create-home --home-dir /opt/rarebirdalert --shell /usr/sbin/nologin rarebirdalert
sudo -u rarebirdalert git clone https://github.com/steven-streller/RareBirdAlert.git /opt/rarebirdalert/app
cd /opt/rarebirdalert/app
sudo -u rarebirdalert python3 -m venv /opt/rarebirdalert/venv
sudo -u rarebirdalert /opt/rarebirdalert/venv/bin/pip install -r requirements.txt
sudo mkdir -p /opt/rarebirdalert/data
sudo chown rarebirdalert:rarebirdalert /opt/rarebirdalert/data
```

## Session-Secret

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Das Ergebnis brauchst du gleich für die systemd-Unit.

## systemd-Unit

`/etc/systemd/system/rarebirdalert.service`:

```ini
[Unit]
Description=RareBirdAlert
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rarebirdalert
Group=rarebirdalert
WorkingDirectory=/opt/rarebirdalert/app
Environment=TZ=Europe/Berlin
Environment=RAREBIRDALERT_DB_PATH=/opt/rarebirdalert/data/rarebirdalert.db
Environment=RAREBIRDALERT_AIRCRAFT_DB_CACHE=/opt/rarebirdalert/data/aircraft-db.csv
Environment=SESSION_SECRET_KEY=<hier den generierten Schlüssel eintragen>
Environment=REGISTRATION_ENABLED=true
ExecStart=/opt/rarebirdalert/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

# Defense in depth - the app doesn't need broad filesystem/network access
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/rarebirdalert/data
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rarebirdalert
sudo systemctl status rarebirdalert
journalctl -u rarebirdalert -f
```

Der Dienst horcht hier bewusst nur auf `127.0.0.1` – für Zugriff von außen
einen Reverse Proxy davorsetzen (siehe unten), statt uvicorn direkt an
`0.0.0.0` zu binden.

## Reverse Proxy (Caddy)

Am einfachsten für TLS via Let's-Encrypt-Automatik. `/etc/caddy/Caddyfile`:

```caddyfile
rarebirdalert.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

Alternativ nginx mit einem klassischen `proxy_pass http://127.0.0.1:8000;`
Server-Block plus certbot für TLS.

## Updates

```bash
cd /opt/rarebirdalert/app
sudo -u rarebirdalert git pull
sudo -u rarebirdalert /opt/rarebirdalert/venv/bin/pip install -r requirements.txt
sudo systemctl restart rarebirdalert
```
