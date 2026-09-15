/* VAVE mobile service worker: caches the app shell only, never API data. */

const SHELL = ["./", "index.html", "styles.css", "app.js",
               "manifest.webmanifest", "icon-192.svg", "icon-512.svg"];
const CACHE = "vave-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API traffic always goes to the network. Caching a token-bearing
  // response or a stale task list would be a lie and a leak.
  if (url.pathname.startsWith("/api/") || event.request.method !== "GET") {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (url.origin === location.origin && response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("index.html")))
  );
});
