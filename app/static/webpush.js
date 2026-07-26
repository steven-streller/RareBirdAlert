function rbaUrlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

function rbaSetWebpushStatus(text) {
    const el = document.getElementById("webpush-status");
    if (el) el.textContent = text;
}

async function rbaSubscribeWebPush(csrfToken) {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        rbaSetWebpushStatus("Web Push wird von diesem Browser nicht unterstützt.");
        return;
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
        rbaSetWebpushStatus("Benachrichtigungs-Erlaubnis wurde nicht erteilt.");
        return;
    }

    const reg = await navigator.serviceWorker.register("/static/sw.js");
    const keyResp = await fetch("/push/vapid-public-key");
    const { key } = await keyResp.json();

    const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: rbaUrlBase64ToUint8Array(key),
    });

    await fetch("/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(subscription.toJSON()),
    });
    rbaSetWebpushStatus("Dieses Gerät ist abonniert.");
}

async function rbaUnsubscribeWebPush(csrfToken) {
    if (!("serviceWorker" in navigator)) return;
    const reg = await navigator.serviceWorker.getRegistration("/static/sw.js");
    if (!reg) {
        rbaSetWebpushStatus("Dieses Gerät ist nicht abonniert.");
        return;
    }
    const subscription = await reg.pushManager.getSubscription();
    if (!subscription) {
        rbaSetWebpushStatus("Dieses Gerät ist nicht abonniert.");
        return;
    }

    await fetch("/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    await subscription.unsubscribe();
    rbaSetWebpushStatus("Dieses Gerät ist nicht mehr abonniert.");
}
