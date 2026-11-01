// Minimal PWA service worker: installable + graceful offline for the static shell.
// Network-first everywhere. Pages are dynamic/per-user, so they are never
// precached — future UI changes need no changes here. Only /static assets get
// cached (as a side effect of being fetched) for offline fallback.
const CACHE = "shell-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (req.url.includes("/static/")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});

// --- push notifications (reminders). Payload: {title, body, url}
self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(d.title || "Reminder", {
    body: d.body || "", icon: "/static/icon-192.png", badge: "/static/icon-192.png",
    data: { url: d.url || "/" }, tag: d.url || "reminder",
  }));
});

// tap -> focus an open window (navigating it) or open the app at the url
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if ("focus" in w) { await w.focus(); if ("navigate" in w) await w.navigate(url); return; }
    }
    await clients.openWindow(url);
  })());
});
