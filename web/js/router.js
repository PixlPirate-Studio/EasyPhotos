// Tiny hash router: #/people/12?x=1
const routes = [];
let notFound = null;
let onChange = null;

export function route(pattern, handler) {
  const keys = [];
  const re = new RegExp('^' + pattern.replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '$');
  routes.push({ re, keys, handler });
}

export function fallback(handler) { notFound = handler; }
export function onRoute(fn) { onChange = fn; }

export function current() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs || '')) };
}

export function resolve() {
  const { path, query } = current();
  for (const r of routes) {
    const m = path.match(r.re);
    if (m) {
      const params = {};
      r.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
      onChange && onChange(path);
      return r.handler(params, query);
    }
  }
  onChange && onChange(path);
  return notFound && notFound();
}

export function go(hash) {
  if (location.hash === hash) resolve();
  else location.hash = hash;
}

export function start() {
  window.addEventListener('hashchange', resolve);
  resolve();
}
