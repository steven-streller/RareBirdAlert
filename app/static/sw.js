self.addEventListener("push", (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { title: "RareBirdAlert", body: event.data ? event.data.text() : "" };
    }
    const title = data.title || "RareBirdAlert";
    const options = {
        body: data.body || "",
        data: { url: data.url || "/" },
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = (event.notification.data && event.notification.data.url) || "/";
    event.waitUntil(clients.openWindow(url));
});
