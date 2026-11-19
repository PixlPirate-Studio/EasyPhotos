// Tiny hash router: #/people/12?x=1
// The open photo lives in the address too (?photo=123), so the browser's back
// button and the viewer's close button both just remove it again. Changing
// only the photo never reloads the page underneath.
const routes = [];
let notFound = null;
let onChange = null;
let photoHandler = null;
let lastBase = null;

export function route(pattern, handler) {
  const keys = [];
  const re = new RegExp('^' + pattern.replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '$');
  routes.push({ re, keys, handler });
}

export function fallback(handler) { notFound = handler; }
export function onRoute(fn) { onChange = fn; }
export function onPhoto(fn) { photoHandler = fn; }

export function current() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs || '')) };
}

function split() {
  const { path, query } = current();
  const photo = query.photo || null;
  delete query.photo;
  const qs = new URLSearchParams(query).toString();
  return { path, query, photo, base: path + (qs ? '?' + qs : '') };
}

// Address of the current page with a different (or no) open photo.
export function hashWithPhoto(id) {
  const { path, query } = split();
  if (id) query.photo = String(id);
  const qs = new URLSearchParams(query).toString();
  return '#' + path + (qs ? '?' + qs : '');
}

export function resolve() {
  const { path, query, photo, base } = split();
  if (base === lastBase) {
    // Same page; only the open photo changed (opened, closed, back/forward).
    photoHandler && photoHandler(photo);
    return;
  }
  lastBase = base;
  onChange && onChange(path);
  let result;
  let matched = false;
  for (const r of routes) {
    const m = path.match(r.re);
    if (m) {
      const params = {};
      r.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
      result = r.handler(params, query);
      matched = true;
      break;
    }
  }
  if (!matched) result = notFound && notFound();
  Promise.resolve(result).then(() => { if (photoHandler) photoHandler(photo); });
  return result;
}

export function go(hash) {
  lastBase = null; // always redraw the page
  if (location.hash === hash) resolve();
  else location.hash = hash;
}

export function start() {
  window.addEventListener('hashchange', resolve);
  resolve();
}
