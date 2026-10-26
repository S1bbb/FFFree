// Версию поднимает GitHub Action при обновлении расписания; при ручных правках сайта поднимайте сами
const VERSION = "rooms-2026-10-26h";
const ASSETS = ["/", "/data/schedule.json", "/manifest.webmanifest", "/icons/apple-touch-icon.png", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/favicon-32.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  // Страница и расписание: сначала сеть (свежие данные), без сети — кэш
  const key = req.mode === "navigate" ? "/" : new URL(req.url).pathname === "/data/schedule.json" ? "/data/schedule.json" : null;
  if (key) {
    e.respondWith(fetch(req).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open(VERSION).then(c => c.put(key, copy)); }
      return r;
    }).catch(() => caches.match(key)));
    return;
  }
  // Остальное: кэш, затем сеть
  e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
});
