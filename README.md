# RareBirdAlert

RareBirdAlert beobachtet den Luftraum um deutsche Flughäfen und schickt eine
[Pushover](https://pushover.net/)-Nachricht, wenn ein spannendes Flugzeug im **Anflug** ist oder **gelandet**
ist, etwa die Beluga in Bremen. Die Nachricht enthält, wenn vorhanden, ein Foto von
[Planespotters.net](https://www.planespotters.net/).

Es ist ein einzelnes statisches Go-Binary in einem `FROM scratch`-Image von unter 8 MB, mit strukturierten
Logs für Loki und einem `/metrics`-Endpunkt für Prometheus.

## Schnellstart

Du brauchst ein Pushover-Konto, eine dort angelegte Anwendung (<https://pushover.net/apps/build>) für den
API-Token und deinen User-Key. Für Fotos brauchst du außerdem eine Kontaktangabe (URL oder E-Mail), siehe
[Datenquellen](#datenquellen).

```sh
cp .env.example .env                               # PUSHOVER_TOKEN, PUSHOVER_USER, RAREBIRDALERT_CONTACT eintragen
cp config/config.example.yaml config/config.yaml   # Flughäfen und Watchlist eintragen
podman compose up -d --build                       # oder: docker compose up -d --build

podman exec rarebirdalert /rarebirdalert test                  # Test-Push aufs Handy
podman exec rarebirdalert /rarebirdalert test --hex 3c6444     # dasselbe mit dem Foto dieses Flugzeugs
podman exec rarebirdalert /rarebirdalert now                   # Flugzeuge in den Geofences, welche Regel greift
```

Läuft podman rootless, muss `config/config.yaml` für andere lesbar sein (`chmod 644`), weil der Prozess im
Container als unprivilegierter Benutzer 65534 läuft. Zugangsdaten stehen nicht in dieser Datei.

Nach einem Update baust und startest du neu mit `podman compose up -d --build --force-recreate`. Ohne
`--force-recreate` lässt podman-compose den laufenden Container mit dem alten Image weiterlaufen. Was schon
gemeldet wurde, bleibt im Volume erhalten, ein Neustart wiederholt keine Push.

Dasselbe gilt für `.env`: Umgebungsvariablen liest ein Container nur beim Erzeugen. Änderungen an
`config.yaml` wirken dagegen von selbst.

Läuft etwas nicht, zeigt `podman logs rarebirdalert` den Grund. Kann RareBirdAlert gar nicht erst starten,
etwa weil `PUSHOVER_TOKEN` fehlt, beendet es sich mit einem Fehler, aber frühestens 30 Sekunden nach dem
Start. Die Pause verhindert, dass eine Neustart-Regel den Container mehrmals pro Sekunde neu startet.

**Einstellen.** Mit `RAREBIRDALERT_DRY_RUN=1` entscheidet RareBirdAlert wie immer, loggt aber nur, was es
gesendet hätte (`notification.dry_run`). So lassen sich die Schwellwerte unter `detection` an echten Flügen
prüfen, ohne dass das Handy klingelt.

## Wann kommt eine Push?

Keine der Quellen sagt, wohin ein Flugzeug fliegt. Anflug und Landung werden deshalb aus den Positionen
abgeleitet. Das gilt je Flugzeug und Flughafen, und nur für Flugzeuge, die interessant sind. Interessant ist
ein Flugzeug, wenn

- es auf der **Watchlist** steht (Typ, Kennzeichen, ICAO24-Adresse, Rufzeichen-Präfix, Betreiber), oder
- es in der Community-Datenbank als **militärisch** (`military: true`) oder als **interessant**
  (`interesting: true`) markiert ist, oder
- sein Typ an diesem Flughafen noch nie im Anflug war (**Neuheit**, `novelty`).

| Begriff | Bedeutung |
|---|---|
| Geofence | Kreis um den Flughafen, `radius_nm` (Standard 10 NM). |
| Anflug | Im Geofence, nicht am Boden, höchstens `approach_max_alt_ft` über dem Platz, sinkend und näherkommend, **in zwei Zyklen hintereinander**. |
| Gelandet | Am Boden gemeldet, höchstens `landing_radius_nm` vom Platz. **Oder:** ein Flugzeug im Anflug verschwindet unter 1500 ft Höhe innerhalb 5 NM, dann gilt es als „vermutlich gelandet“. |
| Cooldown | Nach einer Push je Flugzeug und Flughafen für `cooldown` (Standard 3 h) keine weitere Anflug-Push. |

1. **Anflug**: Beginnt der Anflug eines Flugzeugs der Watchlist, kommt eine Push, wenn der Cooldown nicht läuft.
2. **Gelandet**: Wurde der Anflug gemeldet, kommt bei der Landung eine zweite, standardmäßig stille Push.
3. **Nie doppelt**: Durchstarten und Wiederkommen im selben Besuch meldet nichts erneut. Landet ein Flugzeug
   und startet wieder (Touch-and-go), unterdrückt der Cooldown den nächsten Anflug.
4. **Überflüge und Abflüge** lösen nichts aus: Wer hoch fliegt, nicht sinkt oder sich entfernt, ist kein Anflug.
5. **Blindflug**: Liefert keine Quelle aktuelle Daten, schließt RareBirdAlert nichts aus fehlenden Flugzeugen.
6. **Neuheit**: Der erste Anflug eines Typs an einem Flughafen löst eine Push aus, außer in der Lernphase
   (`learning_period`, Standard 14 Tage ab dem ersten Abruf je Flughafen). Dort werden die Typen nur
   mitgeschrieben, sonst wäre am ersten Tag alles neu. Die Pushes für Neuheiten sind standardmäßig lautlos
   (`notify.novelty`). Gemerkt wird ein Typ erst, wenn ein Flugzeug dieses Typs im Anflug war, ein Überflug
   zählt nicht.

Zur Einordnung: ADS-B-Empfang reißt knapp über dem Boden oft ab, besonders an kleineren Flughäfen. Die Landung
ist deshalb teils nur „vermutlich“. Der Anflug ist die verlässlichere Meldung.

So sieht eine Nachricht aus. Ohne Foto führt der Link auf die Karte von adsb.lol.

| | Anflug | Gelandet |
|---|---|---|
| Titel | A3ST im Anflug auf Bremen | A3ST in Bremen gelandet |
| Text | AIRBUS A-300B4-608ST Beluga · A3ST<br>Kennzeichen F-GSTA · Rufzeichen BGA123<br>6,2 NM entfernt · 1.800 ft · sinkt 700 ft/min<br>Treffer: Typ A3ST<br>Quellen: adsb.lol, opensky<br>Foto: Fotograf / Planespotters.net | AIRBUS A-300B4-608ST Beluga · A3ST<br>Kennzeichen F-GSTA · Rufzeichen BGA123<br>Treffer: Typ A3ST<br>Quellen: adsb.lol<br>Foto: Fotograf / Planespotters.net |
| Verfall | nach 30 Minuten | nach 3 Stunden |

Bei Flaggen und Neuheiten steht der Grund in der Zeile "Treffer", etwa "Treffer: Militär C17" oder
"Treffer: neuer Typ an diesem Flughafen B461".

Dazu kommen Meldungen über RareBirdAlert selbst: `config.yaml fehlerhaft`, `Datenquellen nicht erreichbar`
und `Datenquellen wieder erreichbar`.

## Datenquellen

| Quelle | Abfrage | Zugang | Liefert |
|---|---|---|---|
| adsb.lol | `GET /v2/point/{lat}/{lon}/{radius_nm}` | derzeit ohne Schlüssel, Limits dynamisch | Typ, Kennzeichen, Position, Höhe, Rate |
| airplanes.live | dasselbe Format | nicht kommerziell, ohne Zusage, **Kontakt Pflicht** (siehe unten) | wie adsb.lol |
| OpenSky | `GET /states/all` mit Bounding Box | anonym 400 Credits am Tag, mit OAuth2-Client 4000 | Hex, Rufzeichen, Position, Höhe, Rate, **kein Typ** |
| Planespotters.net | `GET /pub/photos/hex/{hex}` | ohne Schlüssel, **Kontakt im User-Agent Pflicht** | Foto, Fotograf, Link |

Die Quellen werden je Flughafen zusammengeführt. Kennzeichen, Typ und Betreiber nimmt RareBirdAlert aus der
ersten Quelle der Liste `sources`, die sie kennt. Position, Höhe und Rate kommen von dem Eintrag, der zuletzt
aktualisiert wurde. adsb.lol und airplanes.live speisen sich aus demselben Community-Netz. Das Zusammenführen
bringt vor allem Ausfallsicherheit, weniger zusätzliche Abdeckung.

- **Kontakt.** `RAREBIRDALERT_CONTACT` (URL oder E-Mail) landet im User-Agent. Planespotters.net antwortet
  sonst mit 403, ebenso airplanes.live. Ohne Kontakt laufen die Pushes ohne Foto weiter, und RareBirdAlert
  warnt beim Start (`app.warning`).
- **airplanes.live** verlangt, dass du dich vorab per E-Mail an contact@airplanes.live meldest und dein
  Projekt beschreibst, bevor Anfragen angenommen werden. Bis dahin lässt du es aus `sources` heraus (das ist
  der Standard).
- **Planespotters-Schlüssel**: Die Photo API kennt keinen. Wer ein Foto zeigt, muss den Fotografen neben dem
  Bild nennen und auf die Foto-Seite verlinken; das tun die Pushes. Das Foto ist als Anhang an der Push
  und die Zeile „Foto: …“ steht im Text.
- **OpenSky** zählt Abrufe in Credits. Der Abstand der Abrufe (`opensky_interval`) wird standardmäßig so
  gewählt, dass höchstens 75 % der Tages-Credits über alle Flughäfen verbraucht werden. Ohne Zugang sind das bei
  zwei Flughäfen rund alle 10 Minuten, mit Zugang rund jede Minute. Läuft das Kontingent aus (429), fragt
  RareBirdAlert bis zum Ablauf der Sperre nicht mehr.

## Konfiguration

### config.yaml

Siehe [config/config.example.yaml](config/config.example.yaml), jede Einstellung ist dort erklärt. Die Datei wird
bei jedem Zyklus auf Änderungen geprüft, ein Neustart ist nicht nötig. Ist sie fehlerhaft, bleibt die letzte
gültige Fassung aktiv und du bekommst eine Push mit der Fehlerstelle. Unbekannte Schlüssel gelten als Fehler, damit
Tippfehler auffallen. Nur `airports` ist Pflicht.

Mounte das Verzeichnis `config/`, nicht die einzelne Datei. Viele Editoren ersetzen die Datei beim Speichern,
und ein Datei-Mount zeigt dann weiter auf die alte Fassung.

Die eingebaute Liste enthält die deutschen Flughäfen und Flugplätze (ICAO `ED…` und `ET…`) aus
[OurAirports](https://ourairports.com/) (gemeinfrei). `rarebirdalert airports EDDW EDDV` zeigt, was darin steht.

### Umgebungsvariablen

| Variable | Standard | Zweck |
|---|---|---|
| `PUSHOVER_TOKEN` | – | API-Token der Pushover-Anwendung, Pflicht (außer im Dry-Run) |
| `PUSHOVER_USER` | – | User- oder Group-Key, Pflicht (außer im Dry-Run) |
| `PUSHOVER_DEVICE` | alle Geräte | nur an dieses Gerät senden |
| `RAREBIRDALERT_CONTACT` | – | URL oder E-Mail für den User-Agent, nötig für Fotos |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | – | OpenSky-OAuth2-Client; ohne beide wird anonym abgefragt |
| `RAREBIRDALERT_CONFIG` | `/config/config.yaml` | Pfad der Konfiguration |
| `RAREBIRDALERT_STATE` | `/data/state.json` | merkt sich, was gemeldet wurde; leer = nicht speichern |
| `RAREBIRDALERT_HTTP_ADDR` | `:8080` | Adresse für `/metrics` und `/healthz`; leer = kein HTTP-Server |
| `RAREBIRDALERT_LOG_LEVEL` | `info` | `debug`, `info`, `warn`, `error` |
| `RAREBIRDALERT_LOG_FORMAT` | `json` | `text` zum Lesen im Terminal |
| `RAREBIRDALERT_DRY_RUN` | aus | `1` = entscheiden und loggen, aber nichts senden |

Ohne State-Datei meldet RareBirdAlert nach einem Neustart laufende Anflüge erneut.

### Kommandos

| Kommando | Zweck |
|---|---|
| `rarebirdalert` | Daemon |
| `rarebirdalert now` | fragt alle Quellen einmal ab und zeigt die Flugzeuge in den Geofences mit Treffer |
| `rarebirdalert airports [ICAO…]` | konfigurierte Flughäfen, oder die genannten aus der eingebauten Liste |
| `rarebirdalert test [--hex HEX]` | Test-Push, prüft die Zugangsdaten; mit `--hex` samt Foto dieses Flugzeugs |
| `rarebirdalert health` | fragt `/healthz` des laufenden Daemons ab, für Healthchecks ohne Shell und curl |
| `rarebirdalert version` | Version |

## Observability

Metriken zeigen, dass etwas passiert ist. Logs zeigen, warum.

| Frage | Antwort |
|---|---|
| Läuft RareBirdAlert und sieht es Daten? | `up`, `rarebirdalert_source_last_success_timestamp_seconds`, `rarebirdalert_source_polls_total` |
| Was ist gerade um die Flughäfen los? | `rarebirdalert_airport_aircraft`, `rarebirdalert_airport_watched_aircraft`, `rarebirdalert_airport_approaching` |
| Welche Quelle taugt wie viel? | `rarebirdalert_source_aircraft`, `rarebirdalert_source_polls_total` |
| Wurde ich benachrichtigt, hat es geklappt? | `rarebirdalert_notifications_total` |
| Warum kam eine Push oder keine? | Logs `aircraft.approach` und `aircraft.landed` mit `decision` und `reason` |
| Ist meine Config aktiv und gültig? | `rarebirdalert_config_last_reload_successful`, Logs `config.loaded` und `config.invalid` |
| Wer ist wann gelandet? | Logs `aircraft.landed` |

### Metriken

`GET /metrics` liefert das Prometheus-Textformat.

| Metrik | Typ | Labels | Bedeutung |
|---|---|---|---|
| `rarebirdalert_source_polls_total` | Counter | `source`, `result` | Abrufe der Quellen |
| `rarebirdalert_source_poll_duration_seconds` | Histogramm | `source` | Dauer eines Abrufs, Buckets 0,05 bis 15 s |
| `rarebirdalert_source_last_success_timestamp_seconds` | Gauge | `source` | Zeitpunkt des letzten erfolgreichen Abrufs |
| `rarebirdalert_source_aircraft` | Gauge | `source`, `airport` | Flugzeuge, die eine Quelle im letzten Abruf gemeldet hat |
| `rarebirdalert_opensky_credits_remaining` | Gauge | – | Credits, die OpenSky für heute noch meldet |
| `rarebirdalert_airport_aircraft` | Gauge | `airport` | Flugzeuge im Geofence, alle Quellen zusammengeführt |
| `rarebirdalert_airport_watched_aircraft` | Gauge | `airport` | davon Flugzeuge der Watchlist |
| `rarebirdalert_airport_approaching` | Gauge | `airport` | Flugzeuge der Watchlist im Anflug |
| `rarebirdalert_approaches_total` | Counter | `airport`, `rule` | erkannte Anflüge, auch wenn der Cooldown die Push verhindert hat |
| `rarebirdalert_landings_total` | Counter | `airport`, `detection` | erkannte Landungen |
| `rarebirdalert_notifications_total` | Counter | `kind`, `result`, `airport` | Push-Versuche |
| `rarebirdalert_photo_lookups_total` | Counter | `result` | Foto-Abfragen |
| `rarebirdalert_config_last_reload_successful` | Gauge | – | 1 = aktuelle config.yaml ist gültig |
| `rarebirdalert_config_last_reload_success_timestamp_seconds` | Gauge | – | Zeitpunkt des letzten gültigen Ladens |
| `rarebirdalert_config_airports` | Gauge | – | Flughäfen in der aktiven Config |
| `rarebirdalert_config_watch_entries` | Gauge | – | Watchlist-Einträge in der aktiven Config |
| `rarebirdalert_build_info` | Gauge | `version`, `goversion` | konstant 1 |
| `go_*`, `process_*` | – | – | Laufzeit und Prozess |

Labelwerte:

- `source`: `adsb.lol`, `airplanes.live`, `opensky`.
- `result` bei Abrufen: `ok`, `timeout`, `network`, `http_status`, `rate_limited`, `invalid_response`.
- `kind`: `approach`, `landed`, dazu `outage`, `recovery` und `config_error` für Meldungen über RareBirdAlert
  selbst (diese tragen kein `airport`-Label).
- `result` bei Pushes: `sent` (angenommen), `failed` (vorübergehender Fehler, wird wiederholt), `rejected`
  (endgültig abgelehnt, wird nicht wiederholt), `dry_run`.
- `rule`: `hex`, `registration`, `type`, `callsign`, `operator`, `military`, `interesting`, `novelty`.
- `detection`: `observed` (am Boden gesehen), `presumed` (Signal knapp über dem Boden verloren).
- `result` bei Fotos: `found`, `not_found`, `error`, `cached`.

Was du wissen solltest:

- **Die Flughafen- und Quellen-Gauges enden bei einem Ausfall.** Sie werden nur ausgeliefert, solange der
  letzte Zyklus höchstens drei Intervalle her ist. Bei einem Ausfall entsteht eine Lücke statt eines
  eingefrorenen Werts.
- **Counter beginnen bei 0**, für jede bekannte Kombination von Labels. `increase()` stimmt damit ab dem
  ersten Ereignis.
- **Labels kommen nur aus festen Mengen.** Hex, Kennzeichen und Typ stehen deshalb in den Logs, nicht in
  Metriken.

Ausdrücke, die sich als Alarme eignen:

```promql
# Keine Quelle hat seit zehn Minuten geantwortet
min(time() - rarebirdalert_source_last_success_timestamp_seconds) > 600

# Die aktuelle config.yaml ist fehlerhaft
rarebirdalert_config_last_reload_successful == 0

# Pushover lehnt Nachrichten ab (Zugangsdaten, Kontingent)
increase(rarebirdalert_notifications_total{result="rejected"}[1h]) > 0
```

Dazu der übliche Alarm auf `up == 0` für den Fall, dass der Prozess nicht läuft.

### Logs

Eine JSON-Zeile je Ereignis auf stdout. Die Felder sind flach und in snake_case, Loki liefert sie mit `| json`
ohne Umbenennung.

- `time`: UTC, RFC 3339
- `level`: `debug`, `info`, `warn`, `error`
- `msg`: kurzer englischer Klartext
- `event`: stabiler Name des Ereignisses, darauf filterst du

Auf `info` stehen nur Zustandswechsel und Entscheidungen. Jeden einzelnen Abruf zeigt erst `debug`. Token und
User-Key werden nie geloggt.

| `event` | Pegel | Wichtige Felder |
|---|---|---|
| `app.started` | info | `version`, `config_path`, `state_path`, `http_addr`, `dry_run`, `photos` |
| `app.stopping` | info, error | `reason` |
| `app.exit_delayed` | info | `delay_seconds` |
| `app.panic` | error | `error`, `stack` |
| `app.warning` | warn | `reason` |
| `http.listening` | info | `http_addr` |
| `http.error` | warn, error | `error` |
| `config.loaded` | info | `config_hash`, `airports`, `sources`, `watch_entries`, `poll_interval`, `reload` |
| `config.invalid` | error | `error`, `config_hash`, `active_config_kept` |
| `state.restored` | info | `tracks`, `discarded` |
| `state.load_failed`, `state.save_failed` | warn | `error` |
| `cycle.completed` | debug | `sources_polled`, `tracks` |
| `source.polled` | debug | `source`, `airport`, `aircraft`, `duration_ms` |
| `source.poll_failed` | warn | `source`, `airport`, `error`, `error_kind`, `http_status`, `consecutive_failures` |
| `source.recovered` | info | `source`, `since`, `outage_seconds`, `failed_polls` |
| `upstream.outage` | error | `since`, `error` |
| `upstream.recovered` | info | `since`, `outage_seconds` |
| `aircraft.approach` | info | `airport`, `hex`, `registration`, `type`, `callsign`, `rule`, `rule_value`, `distance_nm`, `altitude_agl_ft`, `sources`, `decision`, `reason` |
| `aircraft.landed` | info | wie `aircraft.approach`, dazu `presumed` |
| `aircraft.go_around` | info | wie `aircraft.approach`, ohne Entscheidung |
| `aircraft.departed` | info | wie `aircraft.approach`, ohne Entscheidung |
| `aircraft.lost` | info | wie `aircraft.approach`, ohne Entscheidung |
| `notification.sent` | info | `kind`, `airport`, `hex`, `title`, `photo`, `pushover_request`, `duration_ms` |
| `notification.failed` | warn | `kind`, `airport`, `error`, `http_status`, `duration_ms` |
| `notification.rejected` | error | `kind`, `airport`, `error`, `http_status`, `duration_ms` |
| `notification.dry_run` | info | `kind`, `airport`, `title`, `body`, `photo` |
| `photo.found` | info | `hex`, `registration`, `photographer` |
| `photo.missing` | info | `hex`, `registration` |
| `photo.failed` | warn | `hex`, `registration`, `error` |
| `library.log` | warn | Meldung einer Bibliothek in `msg` |

`decision` ist `notify_approach`, `notify_landed` oder `none`. Bei `none` nennt `reason` den Grund:

| `reason` | Bedeutung |
|---|---|
| `cooldown` | für dieses Flugzeug und diesen Flughafen lief noch der Cooldown |
| `already_notified` | in diesem Besuch wurde schon gemeldet |
| `approach_not_notified` | gelandet, aber der Anflug wurde (wegen Cooldown) nicht gemeldet |
| `learning` | Neuheit in der Lernphase: der Typ wurde nur mitgeschrieben |

Abfragen für Loki. Den Selektor am Anfang musst du an die Labels deines Log-Agents anpassen.

```logql
# Warum kam (k)eine Push für die Beluga?
{container="rarebirdalert"} | json | event=~"aircraft.approach|aircraft.landed" | type="A3ST"

# Alle gesendeten Pushes
{container="rarebirdalert"} | json | event="notification.sent" | line_format "{{.title}}"

# Wer ist wann gelandet
{container="rarebirdalert"} | json | event="aircraft.landed" | line_format "{{.airport}}: {{.type}} {{.registration}}"

# Alles, was nicht nach Plan lief
{container="rarebirdalert"} | json | level=~"warn|error"
```

### Dashboard

[grafana/rarebirdalert-dashboard.json](grafana/rarebirdalert-dashboard.json) zeigt die Flugzeuge im Geofence je
Flughafen, Watchlist und Anflüge als Zeitleiste, die Gesundheit der Quellen, gesendete Pushes, Fotos, den Betrieb
und drei Log-Ansichten. Gesendete Pushes erscheinen zusätzlich als Markierung in allen Diagrammen.

Oben wählst du die Prometheus- und die Loki-Datenquelle, den Job und den Log-Selektor. Der Log-Selektor steht
auf `{container="rarebirdalert"}` und hängt davon ab, welche Labels dein Log-Agent vergibt.

### Anbindung an kube-prometheus-stack und Loki

Die Schnipsel sind Beispiele, Namen und Labels musst du anpassen.

**Scrape.** Ein Service mit benanntem Port und ein ServiceMonitor:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: rarebirdalert
  labels:
    release: kube-prometheus-stack   # was dein Prometheus auswählt
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: rarebirdalert
  endpoints:
    - port: metrics                  # Name des Service-Ports, der auf 8080 zeigt
      interval: 30s
```

**Dashboard.** Der Grafana-Sidecar des Stacks lädt Dashboards aus ConfigMaps mit dem Label
`grafana_dashboard: "1"`:

```sh
kubectl create configmap rarebirdalert-dashboard --from-file=grafana/rarebirdalert-dashboard.json
kubectl label configmap rarebirdalert-dashboard grafana_dashboard=1
```

**Betrieb in Kubernetes.**

- Genau eine Replica mit `strategy: Recreate`. Zwei Pods gleichzeitig würden doppelt pushen.
- `config.yaml` als ConfigMap nach `/config` mounten (als Verzeichnis).
- `PUSHOVER_TOKEN`, `PUSHOVER_USER`, `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` aus einem Secret,
  `RAREBIRDALERT_CONTACT` als Umgebungsvariable.
- `/data` auf ein kleines PersistentVolume legen. Mit `emptyDir` kann nach einem Pod-Neustart ein laufender
  Anflug einmal doppelt gemeldet werden.
- Liveness-Probe auf `GET /healthz`. Der Endpunkt meldet 503, wenn die Abruf-Schleife länger als drei
  Intervalle nicht durchgelaufen ist. Er hängt bewusst nicht an den Quellen, damit deren Ausfall keinen
  Neustart auslöst.
- Das Image läuft als Benutzer 65534 ohne Root-Rechte und kommt mit `readOnlyRootFilesystem: true` und ohne
  Capabilities aus.

## Image

`FROM scratch` hat weder Zertifikate noch Zeitzonendaten noch eine Shell. Das Image enthält deshalb genau
drei Dinge: das Binary, die CA-Zertifikate aus dem Basis-Image und ein leeres Verzeichnis `/data` für die
State-Datei. Die Zeitzonendaten stecken im Binary, der Healthcheck ist ein Unterkommando.

Das Binary ist rund 7,8 MB groß (linux/amd64, Go 1.27, `-s -w`, ohne HTTP/2-Client). Die Abhängigkeiten sind
auf zwei Module begrenzt: die Metrik-Bibliothek `VictoriaMetrics/metrics` und ein YAML-Parser. Alles andere,
darunter Multipart für den Foto-Anhang und das OAuth2-Token von OpenSky, kommt aus der Standardbibliothek. Die
Liste der deutschen Flughäfen ist als gut 20 KB große CSV eingebettet.

```sh
podman build -t rarebirdalert --build-arg VERSION=1.0.0 .
podman build -t rarebirdalert --platform linux/arm64 .   # Cross-Build, die Build-Stage läuft nativ
```

## CI und fertige Images

Der Workflow [.github/workflows/ci.yml](.github/workflows/ci.yml) läuft bei jedem Push und jedem Pull
Request:

1. **Tests**: `gofmt`, `go vet`, die Tests mit Race-Detector und noch einmal so, wie das Image gebaut wird
   (ohne cgo, mit den Build-Tags). Go-Version und Build-Tags liest der Workflow aus dem Dockerfile.
2. **Image**: baut für amd64, startet das Image einmal und prüft seine Größe gegen ein Budget von 10 MB. Danach
   baut er für amd64 und arm64.

Veröffentlicht wird nach `ghcr.io/<besitzer>/rarebirdalert`, und nur bei Pushes:

| Auslöser | Tags des Images | `rarebirdalert version` |
|---|---|---|
| Push auf `main` | `edge` | `edge-<Commit>` |
| Tag `v1.2.3` | `1.2.3`, `1.2`, `latest` | `1.2.3` |
| Pull Request | keine, es wird nur gebaut | – |

Nichts, was der Build heranzieht, steht auf einer beweglichen Version wie `latest`: Actions sind auf Commits
festgelegt, das Basis-Image auf Tag und Digest, Go-Module auf Version und Prüfsumme. Dependabot schlägt
Aktualisierungen vor. Runner-Image, buildx und BuildKit hebst du von Hand an.

## Entwicklung

```sh
go test ./...
RAREBIRDALERT_CONFIG=config/config.yaml go run . now

# Daemon lokal, mit PUSHOVER_TOKEN und PUSHOVER_USER in der Umgebung:
RAREBIRDALERT_CONFIG=config/config.yaml RAREBIRDALERT_STATE= RAREBIRDALERT_LOG_FORMAT=text \
  RAREBIRDALERT_DRY_RUN=1 go run .
```

| Paket | Aufgabe |
|---|---|
| `internal/aircraft` | gemeinsames Flugzeugmodell, Zusammenführen der Quellen |
| `internal/airports` | eingebettete Flughafenliste, Entfernungen |
| `internal/source` | Clients von adsb.lol / airplanes.live (ein Client) und OpenSky |
| `internal/photos` | Planespotters-Client mit Zwischenspeicher |
| `internal/config` | `config.yaml` laden und prüfen, Watchlist |
| `internal/engine` | Anflug- und Landungserkennung ohne I/O, dazu die State-Datei |
| `internal/pushover` | Pushover-Client mit Fehlerklassifikation und Bild-Anhang |
| `internal/obs` | Log-Format, Ereignisnamen, Metriken, HTTP-Endpunkte |
| `main` | Abruf-Schleife, Push-Texte, Kommandos |

Metriknamen und Ereignisnamen sind eine Schnittstelle, an der Dashboards und Abfragen hängen. Tests halten sie
fest: Der Metrik-Katalog steht in `internal/obs/testdata/metric_families.golden`, und ein Test prüft, dass das
Dashboard und dieses README nur Metriken und Ereignisse nennen, die es gibt.

## Grenzen

- Die Quellen sind Community-Dienste ohne Zusage. Ändert sich ein Format, erkennt RareBirdAlert das als
  fehlgeschlagenen Abruf; fallen alle Quellen aus, kommt nach `outage_alert_after` eine Push.
- Anflug und Landung sind abgeleitet. Ein Flugzeug ohne Höhen- oder Positionsangabe wird nicht erkannt, und
  Flugzeuge ohne ADS-B-Transponder oder mit abgeschaltetem Transponder (Militär) tauchen nicht auf.
- Die Höhe ist die barometrische Höhe der Quelle, gerechnet gegen die Platzhöhe. Bei sehr tiefem oder hohem
  Luftdruck weicht sie um einige Dutzend Fuß ab, was für die Schwellwerte nicht ins Gewicht fällt.
- Ein neuer Flughafen außerhalb der eingebauten Liste braucht `lat`, `lon` und `elevation_ft` in der Config.
- **Neuheit ist laut, wenn man sie lässt.** Auch seltene Kleinflugzeug- und Business-Jet-Varianten sind "neu".
  Ein Flughafen hat einen langen Schwanz solcher Typen, die Neuheiten werden nach der Lernphase also nicht
  schlagartig aufhören. Deshalb sind sie standardmäßig lautlos; eine längere `learning_period` hilft.
- **Typen fehlen oft.** Bei Militärflugzeugen fehlt der Typ in den Daten oft (in einer Stichprobe bei gut
  40 %), und OpenSky kennt keinen. Solche Flugzeuge können keine Neuheit sein, werden aber über `military`
  gefunden.
- **Betreiber fehlen.** `operators` vergleicht mit dem Betreibernamen der Quelle, den adsb.lol im Normalfall
  nicht mitliefert. Wirksam sind `callsign_prefixes`, etwa `BGA` für die Airbus-Transporter.
- **Die Flags sind Daten der Community.** `military` und `interesting` sind nur so gut wie deren Datenbank,
  und militärische Flugzeuge mit abgeschaltetem Transponder fehlen ganz. Hubschrauber im Platzrundenverkehr
  können als Anflug gelten, wenn sie tief sind und sinken. Das lässt sich mit `detection` dämpfen.
