// App start-up: sign-in screens, the shell (top bar, sidebar, phone tab bar) and routes.
import { h, icon, api, toast, errToast, modal, faceUrl, debounce } from './ui.js';
import { state } from './state.js';
import { route, fallback, onRoute, onPhoto, start, go } from './router.js';
import { photoFromAddress } from './viewer.js';
import * as V from './views.js';
import * as A from './admin.js';

const app = document.getElementById('app');

async function boot() {
  let st;
  try { st = await api('/api/auth/state'); }
  catch (e) { app.innerHTML = ''; app.append(h('div', { class: 'empty' }, 'Can’t reach EasyPhotos. Is the container running?')); return; }
  if (st.setup_needed) return setupScreen();
  if (!st.user) return loginScreen();
  state.user = st.user;
  shell();
}

window.addEventListener('ep:signedout', () => { if (state.user) { state.user = null; loginScreen('You were signed out. Please sign in again.'); } });

// ---------------------------------------------------------------- sign in

function loginFrame(form) {
  app.innerHTML = '';
  app.append(h('div', { class: 'login' }, form));
  const first = form.querySelector('input');
  if (first) first.focus();
}

function logoBlock(sub) {
  return h('div', { class: 'logo' }, icon('photos'), h('h1', {}, 'EasyPhotos'), h('p', { class: 'muted', style: { margin: 0 } }, sub));
}

function loginScreen(msg = '') {
  const user = h('input', { class: 'input', name: 'username', autocomplete: 'username', autocapitalize: 'off', required: true });
  const pass = h('input', { class: 'input', type: 'password', name: 'password', autocomplete: 'current-password' });
  const remember = h('input', { type: 'checkbox', checked: true });
  const err = h('div', { class: 'err' + (msg ? '' : ' hidden') }, msg);
  const btn = h('button', { class: 'btn primary', type: 'submit', style: { height: '48px' } }, 'Sign in');
  const form = h('form', {},
    logoBlock('Sign in to see your photos'),
    err,
    h('label', { class: 'field' }, 'Username', user),
    h('label', { class: 'field' }, 'Password', pass),
    h('label', { class: 'check' }, remember, 'Keep me signed in'),
    btn,
    h('p', { class: 'hint', style: { margin: 0, textAlign: 'center' } }, 'New here? Enter the username your admin gave you. You’ll pick your own password next.'));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true; err.classList.add('hidden');
    try {
      const r = await api('/api/auth/login', { method: 'POST', body: { username: user.value.trim(), password: pass.value, remember: remember.checked } });
      if (r.status === 'set_password') return choosePasswordScreen(r.username, remember.checked);
      if (r.status === 'need_password') { err.textContent = 'Enter your password.'; err.classList.remove('hidden'); pass.focus(); return; }
      state.user = r.user;
      shell();
    } catch (ex) { err.textContent = ex.message; err.classList.remove('hidden'); }
    finally { btn.disabled = false; }
  });
  loginFrame(form);
}

function choosePasswordScreen(username, remember) {
  const p1 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', minlength: 8, required: true });
  const p2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', required: true });
  const err = h('div', { class: 'err hidden' });
  const form = h('form', {},
    logoBlock(`Welcome, ${username}! Choose your password.`),
    err,
    h('input', { type: 'text', name: 'username', value: username, autocomplete: 'username', class: 'hidden', readonly: true }),
    h('label', { class: 'field' }, 'New password', h('span', { class: 'hint' }, 'At least 8 characters'), p1),
    h('label', { class: 'field' }, 'Type it again', p2),
    h('button', { class: 'btn primary', type: 'submit', style: { height: '48px' } }, 'Save and sign in'),
    h('a', { href: '#', onclick: (e) => { e.preventDefault(); loginScreen(); }, style: { textAlign: 'center' } }, 'Back'));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (p1.value !== p2.value) { err.textContent = 'The two passwords don’t match.'; err.classList.remove('hidden'); return; }
    try {
      const r = await api('/api/auth/set-password', { method: 'POST', body: { username, password: p1.value, remember } });
      state.user = r.user;
      shell();
    } catch (ex) { err.textContent = ex.message; err.classList.remove('hidden'); }
  });
  loginFrame(form);
}

function setupScreen() {
  const user = h('input', { class: 'input', autocomplete: 'username', autocapitalize: 'off', required: true });
  const p1 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', required: true });
  const p2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', required: true });
  const err = h('div', { class: 'err hidden' });
  const form = h('form', {},
    logoBlock('First-time setup: create the admin account'),
    err,
    h('label', { class: 'field' }, 'Admin username', user),
    h('label', { class: 'field' }, 'Password', h('span', { class: 'hint' }, 'At least 8 characters'), p1),
    h('label', { class: 'field' }, 'Type it again', p2),
    h('button', { class: 'btn primary', type: 'submit', style: { height: '48px' } }, 'Create admin account'));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (p1.value !== p2.value) { err.textContent = 'The two passwords don’t match.'; err.classList.remove('hidden'); return; }
    try {
      const r = await api('/api/auth/setup', { method: 'POST', body: { username: user.value.trim(), password: p1.value } });
      state.user = r.user;
      shell();
      go('#/admin/library');
      toast('Welcome! Add your first photo library to get started.');
    } catch (ex) { err.textContent = ex.message; err.classList.remove('hidden'); }
  });
  loginFrame(form);
}

// ---------------------------------------------------------------- shell

let content, sidebar, tabbar, statusPill;
let routesReady = false;
let pollTimer = null;

function navLink(hash, ic, label, extra = null, cls = '') {
  return h('a', { href: hash, 'data-nav': hash, class: cls }, ic ? icon(ic) : null, label, extra);
}

function shell() {
  const u = state.user;
  app.innerHTML = '';
  const search = buildSearch();
  statusPill = u.is_admin ? h('a', { class: 'status-pill hidden', href: '#/admin/library' }, h('span', { class: 'dot' }), h('span', { class: 'txt' }, '')) : null;
  const avatar = h('button', { class: 'avatar', 'aria-label': 'Account menu', onclick: (e) => { e.stopPropagation(); accountMenu(avatar); } }, u.username.slice(0, 2));
  const top = h('header', { class: 'topbar' },
    h('a', { class: 'brand', href: '#/' }, icon('photos'), h('span', {}, 'EasyPhotos')),
    search,
    h('div', { style: { marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '10px' } }, statusPill, avatar));
  sidebar = h('nav', { class: 'sidebar', 'aria-label': 'Main' },
    navLink('#/', 'photos', 'Photos'),
    navLink('#/people', 'people', 'People'),
    navLink('#/map', 'map', 'Map'),
    navLink('#/calendar', 'calendar', 'Calendar'),
    navLink('#/albums', 'album', 'Albums'),
    navLink('#/tags', 'tag', 'Tags'),
    navLink('#/favorites', 'heart', 'Favorites'),
    u.is_admin ? [
      h('div', { class: 'sep' }),
      h('div', { class: 'label' }, 'Library'),
      navLink('#/admin/library', 'folder', 'Folders & scanning'),
      navLink('#/admin/faces', 'scan', 'Faces', h('span', { class: 'badge orange hidden', id: 'faces-badge' })),
      navLink('#/admin/duplicates', 'dup', 'Duplicates', h('span', { class: 'badge hidden', id: 'dup-badge' })),
      navLink('#/admin/hidden', 'eyeoff', 'Hidden'),
      navLink('#/admin/recycle', 'trash', 'Recycle Bin'),
      h('div', { class: 'sep' }),
      h('div', { class: 'label' }, 'Admin'),
      navLink('#/admin/users', 'users', 'Users'),
      navLink('#/admin/apikeys', 'key', 'API keys'),
      navLink('#/admin/settings', 'settings', 'Settings'),
      navLink('#/admin/logs', 'file', 'Logs'),
    ] : null);
  tabbar = h('nav', { class: 'tabbar', 'aria-label': 'Main' },
    navLink('#/', 'photos', 'Photos'),
    navLink('#/people', 'people', 'People'),
    navLink('#/map', 'map', 'Map'),
    navLink('#/calendar', 'calendar', 'Calendar'),
    navLink('#/more', 'menu', 'More'));
  content = h('main', { class: 'content', id: 'content' });
  app.append(h('div', { class: 'shell' }, top, h('div', { class: 'body' }, sidebar, content), tabbar));
  onRoute((path) => {
    document.querySelectorAll('[data-nav]').forEach(a => {
      const n = a.dataset.nav.slice(1);
      const on = n === '/' ? (path === '/' || path === '') : path === n || path.startsWith(n + '/');
      a.classList.toggle('on', on);
    });
    if (['/albums', '/tags', '/favorites', '/admin'].some(p => path.startsWith(p)) || path === '/more') tabbar.querySelector('[data-nav="#/more"]').classList.add('on');
  });
  if (!routesReady) { registerRoutes(); onPhoto(photoFromAddress); start(); routesReady = true; } else { go(location.hash || '#/'); }
  if (u.is_admin) { pollStatus(); clearInterval(pollTimer); pollTimer = setInterval(pollStatus, 15000); }
  api('/api/admin/settings').then(s => { state.allowDelete = s.allow_delete; }).catch(() => { state.allowDelete = false; });
}

function accountMenu(anchor) {
  document.querySelector('.menu')?.remove();
  const u = state.user;
  const roleName = { admin: 'Admin', user: 'User (view + download)', viewer: 'Viewer (view only)' }[u.role];
  const menu = h('div', { class: 'menu', role: 'menu' },
    h('div', { class: 'who' }, h('b', {}, u.username), h('div', { class: 'hint' }, roleName)),
    h('button', { onclick: () => { menu.remove(); changePassword(); } }, icon('lock'), 'Change password'),
    h('button', { onclick: async () => { await api('/api/auth/logout', { method: 'POST' }).catch(() => {}); state.user = null; location.hash = '#/'; loginScreen(); } }, icon('logout'), 'Sign out'));
  document.body.append(menu);
  setTimeout(() => document.addEventListener('click', function off(e) { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', off); } }), 0);
  void anchor;
}

function changePassword() {
  const cur = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
  const n1 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  const n2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  modal({
    title: 'Change password',
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      h('label', { class: 'field' }, 'Current password', cur), h('label', { class: 'field' }, 'New password', n1), h('label', { class: 'field' }, 'Type it again', n2)),
    actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Save', cls: 'primary', run: async () => {
      if (n1.value !== n2.value) { toast('The new passwords don’t match.', true); return true; }
      await api('/api/auth/change-password', { method: 'POST', body: { current: cur.value, new: n1.value } });
      toast('Password changed.');
    } }],
  });
}

function buildSearch() {
  const input = h('input', { type: 'search', placeholder: 'Search people, places, tags…', 'aria-label': 'Search' });
  const wrap = h('div', { class: 'search' }, h('label', {}, icon('search'), input));
  let box = null;
  const close = () => { box && box.remove(); box = null; };
  const run = debounce(async () => {
    const q = input.value.trim();
    if (!q) return close();
    try {
      const r = await api('/api/search/suggest?q=' + encodeURIComponent(q));
      close();
      box = h('div', { class: 'suggest' });
      const add = (sec, items) => { if (items.length) { box.append(h('div', { class: 'sec' }, sec)); items.forEach(i => box.append(i)); } };
      add('People', r.people.map(p => h('a', { href: `#/people/${p.id}`, onclick: close }, p.cover_face_id ? h('img', { src: faceUrl(p.cover_face_id), alt: '', style: { width: '28px', height: '28px', borderRadius: '50%', objectFit: 'cover' } }) : icon('people'), p.name)));
      add('Places', r.places.map(p => h('a', { href: `#/place?city=${encodeURIComponent(p.city)}&state=${encodeURIComponent(p.state || '')}`, onclick: close }, icon('map'), [p.city, p.state].filter(Boolean).join(', '))));
      add('Tags', r.tags.map(t => h('a', { href: `#/tags/${t.id}`, onclick: close }, icon('tag'), t.name)));
      add('Albums', r.albums.map(a => h('a', { href: `#/albums/${a.id}`, onclick: close }, icon('album'), a.name)));
      box.append(h('a', { href: `#/search?q=${encodeURIComponent(q)}`, onclick: close }, icon('search'), `Search everything for “${q}”`));
      wrap.append(box);
    } catch (e) { /* ignore */ }
  }, 200);
  input.addEventListener('input', run);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && input.value.trim()) { close(); go(`#/search?q=${encodeURIComponent(input.value.trim())}`); input.blur(); } if (e.key === 'Escape') close(); });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
  return wrap;
}

async function pollStatus() {
  if (!statusPill || !state.user?.is_admin) return;
  try {
    const j = await api('/api/admin/jobs');
    const c = j.counts;
    let text = 'Library up to date', busy = false;
    if (j.paused) { text = 'Background work paused'; busy = false; }
    else if (c.meta_pending) { text = `Making thumbnails · ${c.meta_pending.toLocaleString()} left`; busy = true; }
    else if (j.faces_started && c.faces_pending) { text = `Finding faces · ${c.faces_pending.toLocaleString()} left`; busy = true; }
    else if (j.status.scan && j.status.scan.state === 'running') { text = 'Scanning folders…'; busy = true; }
    statusPill.classList.remove('hidden');
    statusPill.querySelector('.dot').classList.toggle('busy', busy);
    statusPill.querySelector('.txt').textContent = text;
    const fb = document.getElementById('faces-badge');
    const counts = await api('/api/admin/faces?tab=needs&limit=1');
    if (fb) { fb.textContent = counts.counts.needs; fb.classList.toggle('hidden', !counts.counts.needs); }
    const dups = await api('/api/admin/duplicates?limit=1');
    const n = (dups.counts['exact:open'] || 0) + (dups.counts['similar:open'] || 0);
    const db = document.getElementById('dup-badge');
    if (db) { db.textContent = n; db.classList.toggle('hidden', !n); }
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- routes

let cleanup = null;
function view(fn) {
  return async (params, query) => {
    if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
    document.querySelector('.selbar')?.remove();
    content.innerHTML = '';
    window.scrollTo(0, 0);
    try { cleanup = await fn(content, params, query) || null; }
    catch (e) { errToast(e); content.append(h('div', { class: 'empty' }, e.message)); }
  };
}

function adminOnly(fn) {
  return view((el, p, q) => state.user.is_admin ? fn(el, p, q) : el.append(h('div', { class: 'empty' }, 'Only an admin can see this page.')));
}

function registerRoutes() {
  route('/', view(V.photos));
  route('/people', view(V.people));
  route('/people/:id', view(V.person));
  route('/map', view(V.map));
  route('/calendar', view(V.calendar));
  route('/day/:day', view(V.day));
  route('/albums', view(V.albums));
  route('/albums/:id', view(V.album));
  route('/tags', view(V.tags));
  route('/tags/:id', view(V.tag));
  route('/favorites', view(V.favorites));
  route('/place', view(V.place));
  route('/search', view(V.search));
  route('/memories/:year', view(V.memory));
  route('/more', view(V.more));
  route('/admin/library', adminOnly(A.library));
  route('/admin/folder', adminOnly(A.folder));
  route('/admin/faces', adminOnly(A.faces));
  route('/admin/faces/:id', adminOnly(A.faceGroup));
  route('/admin/duplicates', adminOnly(A.duplicates));
  route('/admin/hidden', adminOnly(A.hidden));
  route('/admin/recycle', adminOnly(A.recycle));
  route('/admin/users', adminOnly(A.users));
  route('/admin/apikeys', adminOnly(A.apikeys));
  route('/admin/settings', adminOnly(A.settings));
  route('/admin/logs', adminOnly(A.logs));
  fallback(view((el) => el.append(h('div', { class: 'empty' }, 'Page not found.'))));
}

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
boot();
