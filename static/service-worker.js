const CACHE_NAME = "praktijk-schitter-static-v1.4.0";
const STATIC_ASSETS = [
  "/static/style.css",
  "/static/logo.png",
  "/static/favicon.ico",
  "/static/favicon.png",
  "/static/about-ai-assistant.png",
  "/static/pwa/icon-192.png",
  "/static/pwa/icon-512.png",
  "/static/pwa/icon-maskable-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Privacy rule: only versioned/static assets are cached.
  // Employee pages, credentials, API calls and other application data always use the network.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});
