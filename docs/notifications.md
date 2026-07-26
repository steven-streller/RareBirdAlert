# Benachrichtigungskanäle einrichten

Alle Kanäle werden pro Account in den **Einstellungen** konfiguriert, nicht
über Umgebungsvariablen. Mehrere Kanäle können gleichzeitig aktiv sein – bei
einer passenden Sichtung gehen alle aktivierten Kanäle gleichzeitig raus.
Jeder Kanal hat einen eigenen "Testen"-Button, der mit dem zuletzt
gespeicherten Stand eine Testbenachrichtigung schickt.

## Ruhezeiten

Unter **Einstellungen → Ruhezeiten** lässt sich pro Account ein
Zeitfenster (mit Zeitzone) festlegen, in dem keine Benachrichtigungen
verschickt werden - z. B. 22:00-07:00, um nachts nicht geweckt zu werden.
Beginn nach Ende (wie im Beispiel) gilt als über Mitternacht andauernd.

Sichtungen während der Ruhezeit werden **nicht verworfen**: sie gelten
weiterhin als "noch nicht benachrichtigt" und werden beim nächsten
30-Sekunden-Prüfzyklus nach Ende der Ruhezeit ganz normal zugestellt - kein
Digest, keine Sammel-Benachrichtigung, nur eine Verzögerung. Eine
ungültige Zeitzone fällt automatisch auf UTC zurück, statt die
Benachrichtigung fehlschlagen zu lassen.

## Pushover

1. Account auf [pushover.net](https://pushover.net) anlegen, App auf dem
   Handy installieren.
2. Den **User Key** findest du direkt auf der Pushover-Startseite nach dem
   Login.
3. Unter [pushover.net/apps/build](https://pushover.net/apps/build) eine neue
   Application anlegen (Name frei wählbar, z.B. "RareBirdAlert") – daraus
   ergibt sich der **API Token**.
4. Beide Werte in den Einstellungen bei Pushover eintragen, aktivieren,
   Testen.

## ntfy

1. Einen Topic-Namen ausdenken – etwas Unrätbares wie `rarebirdalert-x7f2q`,
   denn öffentliche ntfy-Topics sind für jeden mit dem Namen mitlesbar.
2. **Server-URL**: entweder das öffentliche `https://ntfy.sh` verwenden, oder
   eine eigene ntfy-Instanz betreiben und deren URL eintragen.
3. **Zugriffstoken** nur nötig, falls das Topic auf der eigenen Instanz per
   ACL geschützt ist.
4. In der ntfy-App (iOS/Android/Web) das gleiche Topic abonnieren.

## Telegram

1. Mit [@BotFather](https://t.me/BotFather) in Telegram chatten, `/newbot`
   senden, Namen vergeben – BotFather gibt dir den **Bot Token**.
2. **Chat ID** herausfinden: dem neuen Bot einmal irgendeine Nachricht
   schreiben, dann im Browser
   `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen und den Wert bei
   `"chat":{"id": ...}` ablesen. Alternativ [@userinfobot](https://t.me/userinfobot)
   nach der eigenen ID fragen.
3. Bot Token + Chat ID eintragen.

## Discord

1. Im Discord-Server: Kanal-Einstellungen → Integrationen → Webhooks → Neuer
   Webhook, Kanal auswählen.
2. Die **Webhook-URL** kopieren und eintragen.

## Generischer Webhook

Für alles, was RareBirdAlert nicht direkt unterstützt (Home Assistant, n8n,
IFTTT Webhooks, ein eigenes Skript, ...). RareBirdAlert schickt einen `POST`
mit JSON-Body:

```json
{
  "title": "EUFI in EDDF",
  "message": "GAF123 (EUFI, Luftwaffe) ist in Frankfurt Main Airport gelandet. Erkannt als: Eurofighter Typhoon",
  "url": null
}
```

Einfach die URL eintragen, die diesen Payload entgegennimmt.

## E-Mail (SMTP)

Host, Port, Benutzer, Passwort, Absender- und Empfänger-Adresse eintragen.
STARTTLS ist standardmäßig aktiv (Port 587); bei Port 465 nutzt
RareBirdAlert automatisch eine direkte TLS-Verbindung statt STARTTLS.

Bei Gmail als Versender: erfordert 2FA auf dem Google-Konto plus ein
[App-Passwort](https://myaccount.google.com/apppasswords) (das normale
Account-Passwort funktioniert nicht mehr für SMTP), Host `smtp.gmail.com`,
Port `587`.

## Web Push (Browser)

Im Gegensatz zu allen anderen Kanälen ist hier **kein externer Account und
keine Zugangsdaten** nötig - Benachrichtigungen kommen direkt vom Server
über die Push-API des Browsers. Nach dem Aktivieren muss zusätzlich jedes
Gerät/jeder Browser einzeln über den Button "Dieses Gerät abonnieren"
abonniert werden (Berechtigungsabfrage des Browsers); "Dieses Gerät
abbestellen" widerruft es wieder, nur für den aktuellen Browser.

**Wichtige Einschränkung, die kein anderer Kanal hat:** Die Push-API von
Browsern verlangt einen "sicheren Kontext" - also **HTTPS**, außer beim
Testen auf `localhost`. Läuft RareBirdAlert hinter einem Reverse-Proxy ohne
TLS-Terminierung, funktioniert dieser eine Kanal nicht, alle anderen Kanäle
sind davon nicht betroffen.

Der zugehörige VAPID-Schlüssel (identifiziert diese Instanz gegenüber den
Push-Diensten der Browser) wird einmalig beim ersten Start automatisch
erzeugt und in der Datenbank gespeichert - keine manuelle Einrichtung
nötig.
