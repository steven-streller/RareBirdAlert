# Datenquellen

RareBirdAlert kann Live-Flugzeugpositionen aus mehreren Datenquellen
gleichzeitig beziehen: [OpenSky Network](https://opensky-network.org/),
[adsb.lol](https://api.adsb.lol/docs) und [airplanes.live](https://airplanes.live/api-guide/).
Alle drei lassen sich unter **Admin → Datenquellen** (nur für den
Admin-Account sichtbar, siehe [Konfiguration](configuration.md#admin-account))
unabhängig voneinander an- und abschalten.

**Wie mehrere aktivierte Quellen zusammenspielen:** Pro Poll-Zyklus wird
jede aktivierte Quelle für jeden beobachteten Flughafen abgefragt; die
Ergebnisse werden nach `icao24` (Transponder-Hexcode) zusammengeführt. Sieht
nur eine Quelle ein Flugzeug, wird es trotzdem berücksichtigt. Sehen beide
Quellen dasselbe Flugzeug, werden fehlende Felder der einen durch die andere
ergänzt (z. B. liefert adsb.lol Typ/Kennung direkt mit, OpenSky nicht) - und
gilt es bei **mindestens einer** Quelle als "am Boden", zählt das als
Landung, selbst wenn die andere Quelle es (noch) als "in der Luft" meldet.
Mehr Quellen bedeuten also bessere Abdeckung, nicht mehr Fehlalarme.

**Prüfen, ob eine Quelle tatsächlich Daten liefert:** die [Karte](index.md#features)
fragt beim Öffnen (und auf Knopfdruck erneut) live ab, was die aktivierten
Quellen gerade um deine beobachteten Flughäfen herum melden, und plottet es
direkt auf der Karte - unabhängig vom Poll-Zyklus des Schedulers.

## OpenSky Network

Gemeinnützige, community-getragene ADS-B-Aggregation. Kein API-Key ist
zwingend erforderlich, aber die anonyme Nutzung ist stark rate-limitiert.

### Anonyme Nutzung vs. eigener Account

Ohne Zugangsdaten läuft jede Anfrage anonym mit einem niedrigen täglichen
Kontingent. Für mehr als ein, zwei beobachtete Flughäfen bei einem kurzen
Poll-Intervall reicht das schnell nicht mehr aus.

Für ein deutlich höheres Kontingent:

1. Kostenlosen Account auf [opensky-network.org](https://opensky-network.org/)
   anlegen.
2. Unter den Account-Einstellungen einen API-Client (OAuth2
   Client-Credentials) registrieren.
3. Client-ID und -Secret **entweder** als `OPENSKY_CLIENT_ID`/
   `OPENSKY_CLIENT_SECRET` in der `.env`/den Container-Umgebungsvariablen
   **oder** direkt unter Admin → Datenquellen eintragen - siehe
   [Konfiguration](configuration.md#datenquellen-und-zugangsdaten) für die
   genaue Präzedenz zwischen beidem.

RareBirdAlert holt sich damit automatisch ein Bearer-Token und erneuert es
selbstständig vor Ablauf.

### Wie Anfragen gezählt werden

Ein Poll-Zyklus macht **einen** OpenSky-Aufruf pro eindeutig beobachtetem
Flughafen (nicht pro Nutzer – beobachten mehrere Accounts denselben
Flughafen, wird trotzdem nur einmal abgefragt, mit dem größten angeforderten
Radius). Faustregel: `Anfragen pro Tag ≈ Anzahl Flughäfen × (86400 /
Poll-Intervall in Sekunden)`. Bei vielen Flughäfen und anonymer Nutzung
entsprechend das Poll-Intervall auf der Admin-Seite erhöhen.

### Grenzen

- **Geblockte/militärische Flugzeuge**: OpenSky zeigt nur, was die
  angeschlossenen Community-Feeder tatsächlich empfangen und was nicht
  serverseitig gefiltert wird. Manche Militärmaschinen fliegen mit
  deaktiviertem oder verschlüsseltem ADS-B-Transponder und tauchen dadurch
  gar nicht erst auf. Das ist einer der Hauptgründe, zusätzlich **adsb.lol**
  zu aktivieren (siehe unten).
- **Abdeckungslücken**: Wie bei jedem ADS-B-Netzwerk hängt die Abdeckung von
  der Dichte der Community-Feeder in der jeweiligen Region ab.
- **Rate-Limits ändern sich gelegentlich** von OpenSky-Seite aus. Bei
  wiederholten `429`-Antworten im Log einfach das Poll-Intervall erhöhen oder
  einen eigenen Account mit API-Client einrichten.
- Liefert **kein** Typ/Kennung direkt in den Live-Daten - dafür ist die
  Flugzeug-Metadatenbank zuständig (siehe unten).

## adsb.lol

Ebenfalls community-betriebene, offene ADS-B-API im Stil von ADSB Exchange -
**kein API-Key nötig**, kein Rate-Limit-Setup erforderlich. Standardmäßig
aktiviert.

Zwei Vorteile gegenüber OpenSky, die adsb.lol als zweite Quelle besonders
wertvoll machen:

- **Liefert Typ (`t`) und Kennung (`r`) direkt live mit** - kein Warten auf
  die wöchentliche Metadatenbank-Aktualisierung nötig, und aktueller bei
  frisch umregistrierten Flugzeugen.
- **`dbFlags`**: adsb.lol markiert Flugzeuge, die als militärisch gelten,
  eine private ICAO-Adresse (PIA) nutzen oder auf der FAA-LADD-Liste stehen
  (Flugzeuge, die bei anderen Trackern absichtlich versteckt werden). Das
  füttert die eingebaute Watchlist-Kategorie **„Militär/Privat-ICAO
  (adsb.lol-Flag)“** - deutlich zuverlässiger als die Callsign-Präfix-
  Heuristik der `military`-Kategorie, weil es aus der Datenbank des
  Flugzeugs kommt statt aus einer Namens-Vermutung. Siehe
  [Watchlist](watchlist.md).

Radius wird intern von km in nautische Meilen umgerechnet (adsb.lol-Limit:
250 nm ≈ 463 km) - ein größerer eingestellter Radius wird entsprechend
gekappt.

### Grenzen

- Kein offizielles SLA/Rate-Limit-Dokument - bei wiederholten Fehlern im Log
  einfach das Poll-Intervall erhöhen.
- Liefert keinen Betreiber (`operator`) - dafür wird weiterhin die
  Flugzeug-Metadatenbank herangezogen.

## airplanes.live

Noch ein community-betriebenes, offenes ADS-B-Netzwerk - technisch praktisch
identisch zu adsb.lol (gleiches JSON-Format, gleicher
`/v2/point/{lat}/{lon}/{radius}`-Endpunkt, gleiche `dbFlags`-Konvention für
Militär/PIA/LADD, siehe `app/adsb_json.py` für den gemeinsam genutzten
Parser). **Kein API-Key nötig**, standardmäßig aktiviert.

### Grenzen

- **Explizites Rate-Limit von 1 Anfrage/Sekunde** (im Gegensatz zu adsb.lol
  offiziell dokumentiert). Bei vielen beobachteten Flughäfen und mehreren
  gleichzeitig aktivierten Quellen entsprechend ein höheres Poll-Intervall
  wählen, damit airplanes.live nicht öfter als einmal pro Sekunde über alle
  Flughäfen hinweg abgefragt wird.
- Liefert keinen Betreiber (`operator`) - dafür wird weiterhin die
  Flugzeug-Metadatenbank herangezogen.

## Flugroute (adsbdb.com)

Zusätzlich zu den Live-Positionsquellen fragt RareBirdAlert bei
[adsbdb.com](https://www.adsbdb.com/) (kostenlos, kein API-Key nötig) die
Flugroute zum Callsign ab - **aber nur für tatsächlich als "besonders"
erkannte Sichtungen**, nicht für jedes gepollte Flugzeug. Ist die Route
bekannt, tauchen Start- und Zielflughafen (ICAO-Code und Name) im
Sichtungs-Feed und in der Benachrichtigung auf, z. B. „Route: EDDF → EDDM“.

Das ist Fahrplan-/Schedule-Wissen zum Callsign, keine Live-Positionsdaten -
bei unbekanntem Callsign, fehlendem Callsign oder einem nicht erreichbaren
adsbdb.com bleibt die Route einfach leer; das ist ein rein optionales
Zusatzfeature und blockiert nie die eigentliche Sichtungs-Erkennung oder
Benachrichtigung.

## Flugzeug-Metadatenbank

Betreiber-Informationen sowie Typ/Kennung als Fallback (wenn keine aktivierte
Quelle sie live mitliefert) kommen aus OpenSkys öffentlicher
Flugzeugdatenbank (CSV-Export, ca. 500.000 Zeilen) - unabhängig davon, welche
Live-Quelle(n) aktiviert sind. RareBirdAlert lädt diese beim ersten Start
(kann bis zu einer Minute dauern) und danach wöchentlich neu in einen
lokalen Cache (`RAREBIRDALERT_AIRCRAFT_DB_CACHE`). Schlägt der Download fehl
(z. B. wegen eines Netzwerkproblems), läuft die Erkennung mit den ggf. live
verfügbaren Feldern weiter und der nächste geplante Versuch holt es nach –
kein harter Fehler.
