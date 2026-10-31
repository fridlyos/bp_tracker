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
