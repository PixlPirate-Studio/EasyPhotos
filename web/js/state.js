// Shared app state.
export const state = {
  user: null,          // { id, username, role, can_download, is_admin }
  allowDelete: true,
};

let leafletPromise = null;
// Loads the map library only when a map is shown.
export function loadLeaflet() {
  if (window.L && window.L.markerClusterGroup) return Promise.resolve(window.L);
  if (leafletPromise) return leafletPromise;
  const css = (href) => { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = href; document.head.append(l); };
  const js = (src) => new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = rej; document.head.append(s); });
  css('/static/vendor/leaflet/leaflet.css');
  css('/static/vendor/markercluster/MarkerCluster.css');
  leafletPromise = js('/static/vendor/leaflet/leaflet.js').then(() => js('/static/vendor/markercluster/leaflet.markercluster.js')).then(() => window.L);
  return leafletPromise;
}

export const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
// OpenStreetMap needs to know which site asks for map pictures; only the site address is sent.
export const TILE_OPTS = { referrerPolicy: 'strict-origin-when-cross-origin', crossOrigin: false };
export const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
