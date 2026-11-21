// Small helpers shared by every page: building elements, icons, dialogs, toasts, API calls.

// Make el.append(...) skip empty values (null/undefined/false) instead of printing "null".
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  const orig = proto.append;
  proto.append = function (...kids) { return orig.apply(this, kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false)); };
}

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') el.innerHTML = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const P = {
  photos: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>',
  people: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14.5c2.3 0 4 1.5 4.5 4"/>',
  map: '<path d="M12 21s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  album: '<path d="M4 6h6l2 2h8v11H4z"/>',
  albumadd: '<path d="M4 6h6l2 2h8v11H4z"/><path d="M12 11v5M9.5 13.5h5"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  tag: '<path d="M20.6 12.6l-8 8a2 2 0 0 1-2.8 0l-6.4-6.4V4h10.2l7 7a2 2 0 0 1 0 2.8z"/><circle cx="8" cy="8" r="1.5"/>',
  folder: '<path d="M3 6h7l2 2h9v11H3z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  down2: '<path d="M6 9l6 6 6-6"/>',
  down: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  share: '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c5 0 9 4.5 10 7a13 13 0 0 1-3 4M6.6 6.6A13 13 0 0 0 2 12c1 2.5 5 7 10 7 1.6 0 3-.4 4.3-1"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  camera: '<rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="12" cy="13.5" r="3.5"/><path d="M8 7l1.5-3h5L16 7"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  file: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  box: '<rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 2"/>',
  scan: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><circle cx="12" cy="10" r="2.5"/><path d="M8 16c.7-1.8 2.2-2.8 4-2.8s3.3 1 4 2.8"/>',
  logout: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>',
  users: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V4H3v13h5"/>',
  recycle: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  dup: '<rect x="3" y="7" width="13" height="13" rx="2"/><path d="M8 7V4h13v13h-5"/>',
  more: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
  restore: '<path d="M4 12a8 8 0 1 0 2.3-5.6L4 8.5"/><path d="M4 4v4.5h4.5"/>',
  live: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="6.5" stroke-dasharray="2 2"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
};

export function icon(name, cls = '') {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('class', 'i ' + cls);
  s.setAttribute('aria-hidden', 'true');
  s.innerHTML = P[name] || '';
  return s;
}

export const LOGO = () => icon('photos');

// ---------------------------------------------------------------- API

export class ApiError extends Error {
  constructor(msg, status, data = null) { super(msg); this.status = status; this.data = data; }
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const opts = { method, headers: { 'X-Requested-With': 'EasyPhotos' }, credentials: 'same-origin', signal };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('json')) data = await res.json(); else data = await res.text();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth')) { window.dispatchEvent(new CustomEvent('ep:signedout')); }
    throw new ApiError((data && data.error) || `Something went wrong (${res.status})`, res.status, data);
  }
  return data;
}

export const thumbUrl = (id, v = 0) => `/api/assets/${id}/thumb?v=${v}`;
export const previewUrl = (id, v = 0) => `/api/assets/${id}/preview?v=${v}`;
export const faceUrl = (id) => `/api/faces/${id}/crop`;

// ---------------------------------------------------------------- toasts & dialogs

export function toast(msg, isError = false) {
  let box = document.querySelector('.toasts');
  if (!box) { box = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
  const t = h('div', { class: 'toast' + (isError ? ' err' : '') }, msg);
  box.append(t);
  setTimeout(() => t.remove(), isError ? 6000 : 3200);
}

export function errToast(e) { toast(e && e.message ? e.message : String(e), true); }

export function modal({ title, body, actions = [], wide = false, onClose }) {
  const back = h('div', { class: 'modal-back' });
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const box = h('div', { class: 'modal' + (wide ? ' wide' : ''), role: 'dialog', 'aria-label': title },
    h('div', { class: 'mhead' }, h('h2', {}, title),
      h('button', { class: 'iconbtn', 'aria-label': 'Close', onclick: close }, icon('x'))),
    body,
    actions.length ? h('div', { class: 'mfoot' }, actions.map(a => {
      const b = h('button', { class: 'btn ' + (a.cls || ''), type: 'button' }, a.label);
      b.addEventListener('click', async () => {
        if (!a.run) return close();
        b.disabled = true;
        try { const keep = await a.run(b); if (keep !== true) close(); }
        catch (e) { errToast(e); }
        finally { b.disabled = false; }
      });
      return b;
    })) : null);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  back.append(box);
  document.body.append(back);
  document.addEventListener('keydown', onKey);
  const first = box.querySelector('input,select,textarea');
  if (first) setTimeout(() => first.focus(), 30);
  return { close, box };
}

export function confirmDialog({ title, text, confirmLabel = 'OK', danger = false, typeWord = null }) {
  return new Promise((resolve) => {
    let done = false;
    const input = typeWord ? h('input', { class: 'input', placeholder: typeWord, autocomplete: 'off' }) : null;
    const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      h('p', { style: { margin: 0, color: 'var(--text2)', lineHeight: 1.5 } }, text),
      typeWord ? h('label', { class: 'field' }, `Type ${typeWord} to confirm`, input) : null);
    const m = modal({
      title, body,
      onClose: () => { if (!done) resolve(false); },
      actions: [
        { label: 'Cancel', cls: 'ghost' },
        { label: confirmLabel, cls: danger ? 'danger' : 'primary', run: () => {
          if (typeWord && input.value.trim().toUpperCase() !== typeWord) { toast(`Type ${typeWord} to confirm.`, true); return true; }
          done = true; resolve(true);
        } },
      ],
    });
    if (input) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') m.box.querySelector('.mfoot .btn:last-child').click(); });
  });
}

export function promptDialog({ title, label, value = '', placeholder = '', confirmLabel = 'Save' }) {
  return new Promise((resolve) => {
    let done = false;
    const input = h('input', { class: 'input', value, placeholder });
    const m = modal({
      title, body: h('label', { class: 'field' }, label, input),
      onClose: () => { if (!done) resolve(null); },
      actions: [{ label: 'Cancel', cls: 'ghost' }, { label: confirmLabel, cls: 'primary', run: () => { done = true; resolve(input.value.trim()); } }],
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') m.box.querySelector('.mfoot .btn:last-child').click(); });
  });
}

// ---------------------------------------------------------------- formatting

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export { MONTHS, MONTHS_LONG, DAYS };

export function parseLocal(t) {
  if (!t) return null;
  const m = t.match(/(\d{4})-(\d{2})-(\d{2})T?(\d{2})?:?(\d{2})?/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
}

export function fmtDay(dayStr) {
  const d = parseLocal(dayStr);
  if (!d) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  const base = `${DAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
  if (diff === 0) return 'Today · ' + base;
  if (diff === 1) return 'Yesterday · ' + base;
  return d.getFullYear() === today.getFullYear() ? base : `${base}, ${d.getFullYear()}`;
}

export function fmtDateTime(t) {
  const d = parseLocal(t);
  if (!d) return { date: 'Unknown date', time: '' };
  const hh = d.getHours(), mm = String(d.getMinutes()).padStart(2, '0');
  return { date: `${DAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`, time: `${(hh % 12) || 12}:${mm} ${hh < 12 ? 'AM' : 'PM'}` };
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${u[i]}`;
}

export function fmtDuration(s) {
  if (!s) return '';
  s = Math.round(s);
  const m = Math.floor(s / 60), r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

export function fmtAgo(ts) {
  if (!ts) return 'Never';
  const s = Date.now() / 1000 - ts;
  if (s < 90) return 'Just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  if (s < 172800) return 'Yesterday';
  const d = new Date(ts * 1000);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

export function fmtEta(sec) {
  if (!sec) return '';
  if (sec < 3600) return `about ${Math.max(1, Math.round(sec / 60))} min left`;
  if (sec < 86400 * 2) return `about ${Math.round(sec / 3600)} hours left`;
  return `about ${Math.round(sec / 86400)} days left`;
}

export function num(n) { return (n || 0).toLocaleString(); }

export function placeText(a) {
  return [a.city, a.state, a.country].filter(Boolean).join(', ');
}

export function debounce(fn, ms = 250) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function img(src, attrs = {}) {
  const el = h('img', { loading: 'lazy', decoding: 'async', alt: '', ...attrs });
  el.addEventListener('load', () => el.classList.add('ok'));
  el.src = src;
  return el;
}

export function personFace(coverId, size = 64) {
  return coverId ? img(faceUrl(coverId), { width: size, height: size }) : h('div', { class: 'ph' });
}

export function download(url) {
  const a = h('a', { href: url, download: '' });
  document.body.append(a); a.click(); a.remove();
}

// ---------------------------------------------------------------- people with the same name

// Asks whether a typed name means an existing person or a different person who
// shares the name. Resolves { person_id } (same person), { separate: true }, or null (cancel).
export function sameNameDialog(name, choices) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { done = true; m.close(); resolve(v); };
    const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
      h('p', { style: { margin: 0, color: 'var(--text2)' } }, `You already have ${choices.length === 1 ? 'someone' : `${choices.length} people`} named “${name}”. Is this the same person?`),
      choices.map(c => h('button', { class: 'btn', style: { justifyContent: 'flex-start', height: 'auto', padding: '10px 12px' }, onclick: () => finish({ person_id: c.id }) },
        c.cover ? h('img', { src: faceUrl(c.cover), alt: '', style: { width: '48px', height: '48px', borderRadius: '50%', objectFit: 'cover' } }) : null,
        h('span', { style: { display: 'flex', flexDirection: 'column', alignItems: 'flex-start' } }, h('span', {}, `Yes, the same ${c.name}`), h('span', { class: 'hint' }, `${(c.count || 0).toLocaleString()} faces`)))),
      h('button', { class: 'btn primary', onclick: () => finish({ separate: true }) }, `No, a different person also named ${name}`),
      h('span', { class: 'hint' }, 'Different people can share a name. EasyPhotos tells them apart by their faces, not their names.'));
    const m = modal({ title: `Which ${name}?`, body, onClose: () => { if (!done) resolve(null); } });
  });
}

// Runs a naming request; if the name is already used, asks which person is meant and tries again.
// send(extra) must make the request with the extra fields added to its body.
export async function nameSafely(name, send) {
  try { return await send({}); }
  catch (e) {
    if (e.status !== 409 || !e.data || !e.data.choices) throw e;
    const pick = await sameNameDialog(e.data.name || name, e.data.choices);
    if (!pick) return null;
    return await send(pick);
  }
}
