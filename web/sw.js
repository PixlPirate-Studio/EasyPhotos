// Minimal service worker so EasyPhotos can be added to the home screen.
// It does not cache your photos; everything comes live from your server.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
