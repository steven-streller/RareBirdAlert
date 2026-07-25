# Monitoring

RareBirdAlert exponiert Prometheus-Metriken unter `/metrics` - nützlich, um
zu sehen, ob der Poll-Zyklus tatsächlich läuft und ob Benachrichtigungen
ankommen, ohne dafür in die Logs schauen zu müssen.

## Verfügbare Metriken

| Metrik | Typ | Beschreibung |
|---|---|---|
| `rarebirdalert_sightings_total` | Counter | Anzahl erkannter Sichtungen (Landung, die mindestens eine Kategorie/Watchlist-Eintragung getroffen hat). |
| `rarebirdalert_notifications_sent_total{channel,result}` | Counter | Versuchte Benachrichtigungs-Zustellungen, aufgeschlüsselt nach Kanal (`pushover`, `telegram`, ...) und Ergebnis (`ok`/`fail`). |
| `rarebirdalert_poll_duration_seconds` | Histogram | Dauer eines kompletten `poll_job`-Laufs (alle beobachteten Flughäfen, alle aktivierten Datenquellen). |

Dazu kommen die von `prometheus_client` automatisch mitgelieferten
Prozess-Metriken (`process_*`, Python-GC-Statistiken).

## Absichern

Ohne `METRICS_TOKEN` ist `/metrics` offen erreichbar - für einen Scrape aus
einem vertrauenswürdigen internen Netz (z. B. dem gleichen Docker-Netzwerk
oder Kubernetes-Cluster) üblich und unproblematisch. Wird `METRICS_TOKEN`
gesetzt, muss jeder Request einen passenden `Authorization: Bearer
<token>`-Header mitschicken, sonst `401` - Prometheus' `scrape_configs`
unterstützen ein statisches Bearer-Token direkt, ein Cookie-Login wie bei
der Weboberfläche wäre dafür nicht praktikabel.

## Beispiel-Scrape-Konfiguration

```yaml
scrape_configs:
  - job_name: rarebirdalert
    static_configs:
      - targets: ["rarebirdalert:8000"]
    # nur nötig, wenn METRICS_TOKEN gesetzt ist:
    authorization:
      credentials: "dein-token-hier"
```
