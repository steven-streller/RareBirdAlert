# Watchlist

Ein Landeereignis wird nur dann als "Sichtung" gespeichert und meldet sich
bei dir, wenn es mindestens eine eingebaute Kategorie **oder** einen deiner
eigenen Watchlist-Einträge trifft – gewöhnlicher Linienverkehr erzeugt keine
Sichtung.

## Eingebaute Kategorien

Vorkonfigurierte Startpunkte, kein Anspruch auf Vollständigkeit oder
hundertprozentige Trefferquote:

| Kategorie | Kriterium | Hinweis |
|---|---|---|
| Militär | Callsign-Präfix (u. a. `GAF`, `RCH`, `RRR`, `ASCOT`, `NATO`, `CTM`, `IAM`, …) | Erkennt nicht jeden Militärflug – manche fliegen unter zivilem Callsign oder komplett ohne öffentlich sichtbares ADS-B. |
| Eurofighter Typhoon | Flugzeugtyp [`EUFI`](https://skybrary.aero/aircraft/eufi)/[`EFA`](https://skybrary.aero/aircraft/efa) | Unabhängig vom Callsign, direkt über den ICAO-Typcode. |
| Spezial-Transporter | Flugzeugtyp [`A3ST`](https://skybrary.aero/aircraft/a3st) (Beluga), [`A124`](https://skybrary.aero/aircraft/a124) (Antonov An-124), [`C5`](https://skybrary.aero/aircraft/c5)/[`C5M`](https://skybrary.aero/aircraft/c5m) (C-5 Galaxy) | Für die Beluga XL siehe unten – der ICAO-Typcode allein reicht dafür nicht. |
| Historische Klassiker | Flugzeugtyp [`DC3`](https://skybrary.aero/aircraft/dc3)/[`C47`](https://skybrary.aero/aircraft/c47) (Douglas DC-3), [`B17`](https://skybrary.aero/aircraft/b17), [`B29`](https://skybrary.aero/aircraft/b29) | Seltene Oldtimer, die noch flugfähig sind. |
| Militär/Privat-ICAO (adsb.lol-Flag) | `dbFlags`-Bitmaske von adsb.lol (militärisch, PIA, LADD) | Zuverlässiger als die Callsign-Heuristik der `Militär`-Kategorie, da es aus der Flugzeug-Datenbank kommt statt aus einer Namens-Vermutung - aber nur, solange die Datenquelle **adsb.lol** aktiviert ist. Siehe [Datenquellen](data-sources.md). |

Jede Kategorie lässt sich pro Account unter **Watchlist** an- und abschalten
(Standard: alle aktiv).

## Eigene Watchlist-Einträge

Für alles, was die eingebaute Liste nicht abdeckt oder zu grob/zu fein
erfasst. Jeder Eintrag hat ein Kriterium und ein oder mehrere
kommagetrennte Muster:

| Kriterium | Vergleich | Beispiel |
|---|---|---|
| Flugzeugtyp | exakter ICAO-Typcode | [`A339`](https://skybrary.aero/aircraft/a339) |
| Kennung | exakte Registration | `F-GXLG, F-GXLH` |
| ICAO24-Hexcode | exakter Hex-Code des Transponders | `3c6444` |
| Callsign-Präfix | Callsign beginnt mit | `GAF, RCH` |
| Betreiber enthält | Teilstring im Betreiber-Feld | `Antonov` |

### Beispiel: Beluga XL per Kennung erfassen

Der ICAO-Typcode der Beluga XL (Airbus A330-743L) lässt sich in der
OpenSky-Flugzeugdatenbank nicht zuverlässig von einem regulären A330-900
unterscheiden – ein typcode-basierter Eintrag würde also auch normale
A330-900-Linienflüge melden. Zuverlässiger ist ein Eintrag nach Kennung mit
den bekannten Beluga-XL-Registrierungen:

- Kriterium: **Kennung**
- Muster: `F-GXLG, F-GXLH, F-GXLI, F-GXLJ, F-GXLK, F-GXLM`

### Beispiel: alle Antonov-Frachter eines Betreibers

- Kriterium: **Betreiber enthält**
- Muster: `Antonov`

## Wie ein Treffer entsteht

Ein Flugereignis wird gegen **alle** aktuell aktiven eingebauten Kategorien
und **alle** Watchlist-Einträge aller Nutzer geprüft (nicht nur deine
eigenen) – das entscheidet, ob überhaupt eine Sichtung gespeichert wird.
Benachrichtigt wirst du danach nur, wenn der Treffer zu einer bei dir
aktivierten Kategorie oder einem deiner eigenen Einträge gehört, und nur für
Flughäfen, die du selbst beobachtest.

## Vier Zeitpunkte pro Vorgang

Es wird nicht nur bei der eigentlichen Landung bzw. dem Start geprüft,
sondern zusätzlich schon vorher, sobald das möglich ist:

| Ereignis | Zeitpunkt |
|---|---|
| Anflug | Flugzeug ist noch in der Luft, aber sinkt deutlich in der Nähe des Flughafens |
| Landung | Flugzeug hat gerade Bodenkontakt bekommen |
| Startrollen | Flugzeug rollt am Boden bereits merklich schnell (beschleunigt) |
| Start | Flugzeug hat gerade abgehoben |

Pro Vorgang können also bis zu zwei Benachrichtigungen kommen (z. B. erst
"Anflug", kurz danach "Landung") – für dieselbe Maschine, denselben
Flughafen und denselben Treffergrund. "Anflug" und "Startrollen" sind
Heuristiken (Sinkrate bzw. Bodengeschwindigkeit über einem festen
Schwellwert) statt einer exakten Positionsbestimmung, weil dafür keine
Flughafenhöhe im System hinterlegt ist – gelegentliche Fehltreffer oder ein
knapp verpasster Anflug (z. B. bei sehr flachem Sinkflug) sind möglich.
Zeitlich sind auch diese früheren Ereignisse durch das Poll-Intervall
gedeckelt (siehe [Konfiguration](configuration.md)) – bei einer Maschine, die
sehr kurz vor der Landung erst zu sinken beginnt, kommt "Anflug" im
Zweifel erst kurz vor oder sogar gleichzeitig mit "Landung".
