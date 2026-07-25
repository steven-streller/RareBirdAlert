# Podman

Podman läuft rootless standardmäßig ohne Docker-Daemon. Das Image läuft
intern als `appuser` (UID/GID 1000) – rootless Podman mappt diese
Container-UID auf eine Subuid auf dem Host, **nicht** auf UID 1000 des
Hosts. Für ein Bind-Mount muss der Host-Ordner deshalb explizit für diese
gemappte UID beschreibbar gemacht werden.

## Mit `podman run`

```bash
mkdir -p ./data
podman run -d \
  --name rarebirdalert \
  --restart unless-stopped \
  -p 8000:8000 \
  -v ./data:/app/data:Z,U \
  -e TZ=Europe/Berlin \
  -e SESSION_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
  -e REGISTRATION_ENABLED=true \
  ghcr.io/steven-streller/rarebirdalert:latest
```

- `:Z` setzt das passende SELinux-Label (nötig auf Fedora/RHEL/CentOS, auf
  Debian/Ubuntu wirkungslos aber harmlos)
- `:U` lässt Podman den Mount-Ordner rekursiv auf die im Container erwartete
  UID/GID (1000) chownen – das ist der einfachste Weg, das UID-Mapping-Problem
  zu umgehen

## Mit Quadlet (systemd-natives Podman, empfohlen)

Ab Podman 4.4 der idiomatische Weg für dauerhafte Dienste – Podman generiert
die systemd-Unit selbst aus einer `.container`-Datei.

`~/.config/containers/systemd/rarebirdalert.container` (rootless) oder
`/etc/containers/systemd/rarebirdalert.container` (system-weit):

```ini
[Unit]
Description=RareBirdAlert
After=network-online.target

[Container]
Image=ghcr.io/steven-streller/rarebirdalert:latest
PublishPort=8000:8000
Volume=%h/rarebirdalert/data:/app/data:Z,U
Environment=TZ=Europe/Berlin
Environment=REGISTRATION_ENABLED=true
Secret=rarebirdalert-session-secret,type=env,target=SESSION_SECRET_KEY

[Service]
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
mkdir -p ~/rarebirdalert/data
python3 -c "import secrets; print(secrets.token_hex(32))" | podman secret create rarebirdalert-session-secret -

systemctl --user daemon-reload
systemctl --user enable --now rarebirdalert.service
journalctl --user -u rarebirdalert -f
```

Für rootless-Dienste, die auch ohne aktive Login-Session laufen sollen:
`loginctl enable-linger $USER`.

## Mit podman-compose

Podman kann die vorhandene `docker-compose.yml` auch direkt nutzen:

```bash
podman-compose up -d --build
```

Das Bind-Mount-Problem bleibt hier bestehen – ggf. den Host-Ordner vorher mit
`podman unshare chown 1000:1000 ./data` für die gemappte UID vorbereiten.
