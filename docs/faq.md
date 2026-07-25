# FAQ / Troubleshooting

## Ich bekomme keine Benachrichtigungen

Der Reihe nach prüfen:

1. Ist mindestens ein Kanal in den **Einstellungen** aktiviert (Häkchen bei
   "Aktiviert", nicht nur ausgefüllte Felder)? Speichern nicht vergessen.
2. Funktioniert der Kanal über den **"Testen"-Button** der jeweiligen Sektion?
   Wenn nicht: Zugangsdaten prüfen, Logs ansehen (`docker compose logs` bzw.
   `journalctl -u rarebirdalert`).
3. Beobachtest du unter **Flughäfen** überhaupt den betroffenen Flughafen?
4. Ist unter **Watchlist** die passende Kategorie aktiviert oder existiert
   ein eigener Eintrag, der zutrifft?
5. Wurde für diese Sichtung schon einmal benachrichtigt? Pro Sichtung gibt
   es genau eine Benachrichtigung pro Account (kein Spam bei jedem
   Scheduler-Tick).
6. Erreicht RareBirdAlert OpenSky überhaupt? Siehe
   [Datenquelle](data-sources.md) – bei Rate-Limit-Meldungen im Log das
   Poll-Intervall erhöhen oder eigenen OpenSky-Account einrichten.

## Ein bestimmtes Flugzeug wird nicht erkannt

- Prüfen, ob OpenSky es überhaupt sieht: manche Militärmaschinen fliegen mit
  deaktiviertem/verschlüsseltem ADS-B und tauchen bei OpenSky gar nicht auf –
  siehe [Datenquelle](data-sources.md).
- Prüfen, ob der ICAO-Typcode in der eingebauten Kategorie tatsächlich zum
  Flugzeug passt (nicht jeder Sondertyp lässt sich eindeutig per Typcode
  erkennen, z. B. die Beluga XL – siehe [Watchlist](watchlist.md)). Im
  Zweifel einen eigenen Watchlist-Eintrag nach Kennung anlegen.
- Prüfen, ob der beobachtete Radius groß genug ist, um den Anflug/die
  Parkposition zu erfassen (Einstellung beim Hinzufügen des Flughafens).

## "I have no name!" beim Exec in den Container

Der Container läuft als `appuser` (UID/GID 1000), nicht als root. Zeigt eine
interaktive Shell trotzdem "I have no name!", erzwingt die Umgebung (z.B.
Kubernetes `securityContext.runAsUser`) eine andere UID als 1000, für die es
keinen `/etc/passwd`-Eintrag im Image gibt. Siehe
[Kubernetes-Setup](setup/kubernetes.md#non-root) für den nötigen
`securityContext` (inkl. `fsGroup`, damit die Datenbank auch beschreibbar
bleibt).

## Nach jedem Neustart bin ich abgemeldet

`SESSION_SECRET_KEY` ist nicht gesetzt – ohne festen Wert generiert
RareBirdAlert bei jedem Start einen neuen zufälligen Schlüssel, wodurch alle
bestehenden Sessions ungültig werden. Siehe [Konfiguration](configuration.md).

## Der erste Start dauert ungewöhnlich lange

Beim allerersten Start lädt RareBirdAlert die komplette OpenSky-Flugzeug-
datenbank (ca. 500.000 Zeilen) herunter und importiert sie – das kann bis zu
einer Minute dauern. Die Weboberfläche selbst ist währenddessen bereits
erreichbar, nur die Typ-/Betreiber-Erkennung für neue Sichtungen läuft erst
danach vollständig.

## Wer ist Admin, und wie finde ich es heraus?

Der erste jemals registrierte Account ist automatisch Admin und sieht als
einziger den Menüpunkt **Admin** (Poll-Intervall, Datenquellen). Alle
danach registrierten Accounts sind normale Nutzer, ohne Möglichkeit, das
über die Weboberfläche zu ändern.

Nach einem Upgrade von einer Version ohne Admin-Konzept wird der am
längsten bestehende Account beim nächsten Start automatisch zum Admin -
falls das die falsche Person ist (z. B. weil ein Test-Account zufällig
zuerst angelegt wurde), lässt sich das aktuell nur direkt in der
SQLite-Datenbank korrigieren (`UPDATE user SET is_admin = 1 WHERE email =
'...'`, Container vorher stoppen).

## Registrierung geht nicht mehr

`REGISTRATION_ENABLED=false` gesetzt (absichtlich, siehe
[Konfiguration](configuration.md)) – bestehende Accounts können sich
weiterhin einloggen, es kann sich nur niemand Neues mehr registrieren.

## Backup

Die komplette Datenbank ist eine einzelne SQLite-Datei
(`RAREBIRDALERT_DB_PATH`, standardmäßig `/app/data/rarebirdalert.db`). Ein
Backup ist einfach eine Kopie dieser Datei (Container vorher stoppen oder
zumindest kurz pausieren, um mitten in einem Schreibvorgang eine Kopie zu
vermeiden). Der Flugzeug-Metadaten-Cache
(`RAREBIRDALERT_AIRCRAFT_DB_CACHE`) muss nicht gesichert werden – er wird bei
Bedarf automatisch neu heruntergeladen. Wiederherstellen: Datei an die
gleiche Stelle zurückkopieren und den Container neu starten.
