const CACHE_NAME = 'english-quest-shell-v21';
const APP_FILES = ['./', './index.html', './styles.css', './library-data.js', './mission-data.js', './mission-data-extras.js', './access-client.js', './app.js', './manifest.webmanifest', './icon.svg', './icon-192.png', './icon-512.png', './images/aki.webp', './images/rina.webp', './images/suki.webp', './images/naya.webp', './images/bimo.webp'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_FILES.map(file => new URL(file, self.registration.scope).href))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== self.location.origin) return;
  // Never cache account, demo-quota, token-activation or admin API responses.
  const requestPath = new URL(event.request.url).pathname;
  if (/\/api(?:\/|$)/.test(requestPath) || /\/admin\/?$/.test(requestPath)) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).then(response => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(new URL('./index.html', self.registration.scope).href, copy));
      return response;
    }).catch(() => caches.match(new URL('./index.html', self.registration.scope).href)));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)); }
    return response;
  })));
});
