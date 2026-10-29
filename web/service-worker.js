const CACHE_NAME = 'hsx-shell-v1';
const SHELL = ['/web/', '/web/app.js', '/web/manifest.webmanifest', '/web/icon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys
      .filter((key) => key !== CACHE_NAME)
      .map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const requestUrl = new URL(event.request.url);
  if (event.request.method !== 'GET' || requestUrl.pathname.startsWith('/api/')) return;
  if (!requestUrl.pathname.startsWith('/web/')) return;
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
      }
      return response;
    })),
  );
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  const title = String(payload.title || 'Hsx update').replace(/[\r\n]/g, ' ').slice(0, 120);
  event.waitUntil(self.registration.showNotification(title, {
    data: { jobId: String(payload.job_id || '') },
    icon: '/web/icon.svg',
    badge: '/web/icon.svg',
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const jobId = event.notification.data && event.notification.data.jobId;
  const target = jobId ? `/web/?job=${encodeURIComponent(jobId)}` : '/web/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
