# Datenquelle: OpenSky Network

RareBirdAlert bezieht Live-Flugzeugpositionen ausschließlich vom
[OpenSky Network](https://opensky-network.org/), einer gemeinnützigen,
community-getragenen ADS-B-Aggregation. Kein API-Key ist zwingend
erforderlich, aber die anonyme Nutzung ist stark rate-limitiert.

## Anonyme Nutzung vs. eigener Account

Ohne Zugangsdaten (`OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` leer) läuft
jede Anfrage anonym mit einem niedrigen täglichen Kontingent. Für mehr als
ein, zwei beobachtete Flughäfen bei einem kurzen Poll-Intervall reicht das
schnell nicht mehr aus.

Für ein deutlich höheres Kontingent:

1. Kostenlosen Account auf [opensky-network.org](https://opensky-network.org/)
   anlegen.
2. Unter den Account-Einstellungen einen API-Client (OAuth2
   Client-Credentials) registrieren.
3. `OPENSKY_CLIENT_ID` und `OPENSKY_CLIENT_SECRET` in der `.env`/den
   Container-Umgebungsvariablen setzen (siehe [Konfiguration](configuration.md)).

RareBirdAlert holt sich damit automatisch ein Bearer-Token und erneuert es
selbstständig vor Ablauf.

## Wie Anfragen gezählt werden

Ein Poll-Zyklus macht **einen** OpenSky-Aufruf pro eindeutig beobachtetem
Flughafen (nicht pro Nutzer – beobachten mehrere Accounts denselben
Flughafen, wird trotzdem nur einmal abgefragt, mit dem größten angeforderten
Radius). Faustregel: `Anfragen pro Tag ≈ Anzahl Flughäfen × (86400 /
Poll-Intervall in Sekunden)`. Bei vielen Flughäfen und anonymer Nutzung
entsprechend das Poll-Intervall in den Einstellungen erhöhen.

## Grenzen der kostenlosen Nutzung

- **Geblockte/militärische Flugzeuge**: OpenSky zeigt nur, was die
  angeschlossenen Community-Feeder tatsächlich empfangen und was nicht
  serverseitig gefiltert wird. Manche Militärmaschinen fliegen mit
  deaktiviertem oder verschlüsseltem ADS-B-Transponder und tauchen dadurch
  gar nicht erst auf – das ist eine Einschränkung der Datenquelle, keine
  Einschränkung der RareBirdAlert-Erkennungslogik.
- **Abdeckungslücken**: Wie bei jedem ADS-B-Netzwerk hängt die Abdeckung von
  der Dichte der Community-Feeder in der jeweiligen Region ab. In
  bodennahen/gebäudereichen Bereichen (z. B. kurz vor dem Aufsetzen) kann ein
  Flugzeug kurzzeitig aus der Sicht aller Feeder verschwinden.
- **Rate-Limits ändern sich gelegentlich** von OpenSky-Seite aus. Bei
  wiederholten `429`-Antworten im Log einfach das Poll-Intervall erhöhen oder
  einen eigenen Account mit API-Client einrichten.

## Flugzeug-Metadatenbank

Typ, Kennung und Betreiber eines Flugzeugs kommen nicht aus den Live-Daten
selbst, sondern aus OpenSkys öffentlicher Flugzeugdatenbank (CSV-Export, ca.
500.000 Zeilen). RareBirdAlert lädt diese beim ersten Start (kann bis zu
einer Minute dauern) und danach wöchentlich neu in einen lokalen Cache
(`RAREBIRDALERT_AIRCRAFT_DB_CACHE`). Schlägt der Download fehl (z. B. wegen
eines Netzwerkproblems), läuft die Erkennung ohne Typ-Info weiter und der
nächste geplante Versuch holt es nach – kein harter Fehler.
