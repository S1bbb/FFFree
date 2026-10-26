// При обновлении расписания поменяйте версию, чтобы у всех подтянулась новая
const VERSION = "rooms-2026-10-26f";
const ASSETS = ["/", "/manifest.webmanifest", "/icons/apple-touch-icon.png", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/favicon-32.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  // Страница: сначала сеть (свежие данные), без сети — кэш
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then(r => { const copy = r.clone(); caches.open(VERSION).then(c => c.put("/", copy)); return r; }).catch(() => caches.match("/")));
    return;
  }
  // Остальное: кэш, затем сеть
  e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
});
