// Main pages everyone can see.
import { h, icon, api, modal, nameSafely, thumbUrl, faceUrl, img, personFace, toast, errToast, promptDialog, confirmDialog, MONTHS_LONG, DAYS, num, fmtDay } from './ui.js';
import { PhotoGrid } from './grid.js';
import { state, loadLeaflet, TILE_URL, TILE_ATTR, TILE_OPTS } from './state.js';
import { openViewer } from './viewer.js';
import { go } from './router.js';

function head(title, sub = null, actions = []) {
  return h('div', { class: 'page-head' },
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: '4px' } }, h('h1', {}, title), sub ? h('span', { class: 'sub' }, sub) : null),
    actions.length ? h('div', { class: 'actions' }, actions) : null);
}

function gridPage(el, query, opts = {}) {
  const g = new PhotoGrid({ query, ...opts });
  g.mount(el);
  return () => g.destroy();
}

// ---------------------------------------------------------------- Photos (timeline)

export async function photos(el) {
  const strip = h('div', { class: 'people-strip', 'aria-label': 'People' });
  const mem = h('div');
  el.append(strip, mem);
  api('/api/people').then(people => {
    if (!people.length) { strip.remove(); return; }
    people.slice(0, 20).forEach(p => strip.append(h('a', { href: `#/people/${p.id}` }, personFace(p.cover, 64), h('span', {}, p.name))));
  }).catch(() => strip.remove());
  api('/api/memories').then(ms => {
    if (!ms.length) return;
    const now = new Date();
    mem.append(h('div', { style: { marginBottom: '20px' } },
      h('h2', { style: { marginBottom: '10px' } }, 'On this day'),
      h('div', { class: 'memories' }, ms.map(m => h('a', { href: `#/memories/${m.year}` },
        img(thumbUrl(m.ids[0])), h('span', {}, `${now.getFullYear() - m.year} year${now.getFullYear() - m.year === 1 ? '' : 's'} ago`))))));
  }).catch(() => {});
  return gridPage(el, {}, { scrubber: true, emptyText: state.user.is_admin ? 'No photos yet. Add a library in Library › Folders & scanning.' : 'No photos yet.' });
}

export async function memory(el, p) {
  const md = new Date();
  const mmdd = `${String(md.getMonth() + 1).padStart(2, '0')}-${String(md.getDate()).padStart(2, '0')}`;
  el.append(head(`${MONTHS_LONG[md.getMonth()]} ${md.getDate()}, ${p.year}`, 'On this day'));
  return gridPage(el, { day: `${p.year}-${mmdd}` });
}

// ---------------------------------------------------------------- People

export async function people(el, p, q) {
  const showHidden = q && q.hidden === '1';
  let editing = false;
  const list = await api(`/api/people${showHidden ? '?mine_hidden=1' : ''}`);
  const grid = h('div', { class: 'pgrid' });
  const editBtn = h('button', { class: 'btn', onclick: () => { editing = !editing; editBtn.classList.toggle('primary', editing); editBtn.lastChild.textContent = editing ? 'Done' : 'Choose who shows'; draw(); } },
    icon('eyeoff'), h('span', {}, 'Choose who shows'));
  el.append(head(showHidden ? 'Hidden people' : 'People',
    showHidden ? 'People you hid from your People page. Only your account is affected. Tap the eye to bring someone back.' : (list.length ? `${num(list.length)} named` : null),
    [showHidden ? h('a', { class: 'btn', href: '#/settings/me' }, icon('back'), 'Back to settings') : (list.length ? editBtn : null),
      state.user.is_admin && !showHidden ? h('a', { class: 'btn', href: '#/admin/faces' }, icon('scan'), 'Name faces') : null]));
  if (!list.length) {
    el.append(h('div', { class: 'empty' }, icon('people'),
      showHidden ? 'You haven’t hidden anyone.'
        : state.user.is_admin ? h('div', {}, 'No named people yet. Go to ', h('a', { href: '#/admin/faces' }, 'Library › Faces'), ' to name the faces EasyPhotos found.') : 'No people yet.'));
  }
  const toggle = async (person, card) => {
    const hide = !person.hidden_for_me;
    try {
      await api(`/api/people/${person.id}/hide-for-me`, { method: 'POST', body: { hidden: hide } });
      person.hidden_for_me = hide;
      card.remove();
      toast(hide ? `${person.name} won’t show on your People page. Bring them back any time in Settings → Hidden people.` : `${person.name} is back on your People page.`, false,
        { label: 'Undo', run: async () => { await api(`/api/people/${person.id}/hide-for-me`, { method: 'POST', body: { hidden: !hide } }); go(location.hash); } });
    } catch (e) { errToast(e); }
  };
  function draw() {
    grid.innerHTML = '';
    list.filter(pp => showHidden ? pp.hidden_for_me : !pp.hidden_for_me).forEach(pp => {
      const card = h('a', { class: 'pcard' + (editing || showHidden ? ' editing' : ''), href: `#/people/${pp.id}`, style: pp.hidden ? { opacity: 0.55 } : null },
        personFace(pp.cover, 96), h('span', { class: 'n' }, pp.favorite ? h('span', { class: 'fav-star', 'aria-label': 'Favorite' }, '★ ') : null, pp.name),
        h('span', { class: 'hint' }, pp.hidden ? 'Hidden for everyone' : `${num(pp.count)} faces`));
      if (editing || showHidden) {
        card.append(h('button', { class: 'pcard-eye', 'aria-label': showHidden ? `Show ${pp.name} again` : `Hide ${pp.name} from my People`,
          title: showHidden ? 'Show again' : 'Hide from my People',
          onclick: (e) => { e.preventDefault(); e.stopPropagation(); toggle(pp, card); } }, icon(showHidden ? 'eye' : 'eyeoff')));
      }
      grid.append(card);
    });
  }
  draw();
  el.append(grid);
}

// Settings every account has (the admin's Settings page links here too).
export async function mySettings(el) {
  const hidden = await api('/api/people?mine_hidden=1').catch(() => []);
  const row = (hash, ic, label, hint) => h('a', { class: 'card', href: hash, style: { flexDirection: 'row', alignItems: 'center', padding: '14px 16px', color: 'var(--text)' } },
    icon(ic), h('div', { class: 'grow' }, h('b', {}, label), hint ? h('div', { class: 'hint' }, hint) : null), icon('next'));
  el.append(head('My settings', 'These only affect your own account.'),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
      row('#/people?hidden=1', 'eyeoff', 'Hidden people', hidden.length ? `${num(hidden.length)} ${hidden.length === 1 ? 'person' : 'people'} hidden from your People page` : 'Nobody hidden. Use “Choose who shows” on the People page to hide someone.')));
}

export async function person(el, p) {
  const pr = await api(`/api/people/${p.id}`);
  const actions = [];
  const mine = h('button', { class: 'btn', onclick: async () => {
    const hide = !pr.hidden_for_me;
    try {
      await api(`/api/people/${p.id}/hide-for-me`, { method: 'POST', body: { hidden: hide } });
      pr.hidden_for_me = hide;
      mine.lastChild.textContent = hide ? 'Show in my People' : 'Hide from my People';
      toast(hide ? `${pr.name} won’t show on your People page. You can still search for them.` : `${pr.name} is back on your People page.`);
    } catch (e) { errToast(e); }
  } }, icon('eyeoff'), h('span', {}, pr.hidden_for_me ? 'Show in my People' : 'Hide from my People'));
  if (pr.name) actions.push(mine);
  if (state.user.is_admin) {
    const star = h('button', { class: 'btn' + (pr.favorite ? ' primary' : ''), onclick: async () => {
      try {
        await api(`/api/people/${p.id}`, { method: 'PATCH', body: { favorite: !pr.favorite } });
        pr.favorite = !pr.favorite;
        star.classList.toggle('primary', pr.favorite);
        star.lastChild.textContent = pr.favorite ? 'Favorite' : 'Add to favorites';
        toast(pr.favorite ? `${pr.name} is a favorite.` : `${pr.name} is no longer a favorite.`);
      } catch (e) { errToast(e); }
    } }, icon('star'), h('span', {}, pr.favorite ? 'Favorite' : 'Add to favorites'));
    actions.push(star,
      h('button', { class: 'btn', onclick: async () => {
        const name = await promptDialog({ title: 'Rename', label: 'Name', value: pr.name || '' });
        if (!name) return;
        try {
          const r = await nameSafely(name, (extra) => api(`/api/people/${p.id}`, { method: 'PATCH', body: { name, ...extra } }));
          if (!r) return;
          toast(r.merged ? `Merged into ${name}.` : 'Renamed.'); go(`#/people/${r.id}`);
        } catch (e) { errToast(e); }
      } }, icon('edit'), 'Rename'),
      h('button', { class: 'btn', onclick: () => chooseCover(p.id, pr.name, () => go(`#/people/${p.id}`)) }, icon('people'), 'Choose picture'),
      h('a', { class: 'btn', href: `#/admin/faces/${p.id}` }, icon('scan'), 'Review faces'),
      h('button', { class: 'btn ghost', onclick: async () => {
        if (!await confirmDialog({ title: `Remove the name “${pr.name}”?`, text: 'Their faces go back into an unnamed group in Library › Faces, so you can name them again. No photos are changed.', confirmLabel: 'Remove name', danger: true })) return;
        try { await api(`/api/people/${p.id}`, { method: 'PATCH', body: { name: '' } }); toast('Name removed.'); go(`#/admin/faces/${p.id}`); } catch (e) { errToast(e); }
      } }, 'Remove name'),
      h('button', { class: 'btn ghost', onclick: async () => {
        try { await api(`/api/people/${p.id}`, { method: 'PATCH', body: { hidden: !pr.hidden } }); toast(pr.hidden ? 'Shown again for everyone.' : 'Hidden for everyone.'); go('#/people'); } catch (e) { errToast(e); }
      } }, icon(pr.hidden ? 'eye' : 'eyeoff'), pr.hidden ? 'Unhide for everyone' : 'Hide for everyone'));
  }
  el.append(h('div', { class: 'page-head' },
    pr.cover ? h('img', { src: faceUrl(pr.cover), alt: '', style: { width: '64px', height: '64px', borderRadius: '50%', objectFit: 'cover' } }) : null,
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: '4px' } }, h('h1', {}, pr.name || 'Unnamed'), h('span', { class: 'sub' }, `${num(pr.count)} faces`)),
    actions.length ? h('div', { class: 'actions' }, actions) : null));
  return gridPage(el, { person: p.id });
}

// ---------------------------------------------------------------- Map

export async function map(el, p, q) {
  el.append(head('Map'));
  const placesBox = h('div', { class: 'places' }, h('div', { class: 'spinner' }));
  const mapBox = h('div', { id: 'map' });
  el.append(h('div', { class: 'mapwrap' }, placesBox, mapBox));
  const [L, points, places] = await Promise.all([loadLeaflet(), api('/api/map/points'), api('/api/places')]);
  placesBox.innerHTML = '';
  placesBox.append(h('h2', { style: { padding: '0 4px 6px' } }, 'Places'));
  if (!places.length) placesBox.append(h('div', { class: 'hint', style: { padding: '4px' } }, 'No photos with a location yet.'));
  places.forEach(pl => placesBox.append(h('a', { href: `#/place?city=${encodeURIComponent(pl.city)}&state=${encodeURIComponent(pl.state || '')}` },
    img(thumbUrl(pl.cover)), h('div', { style: { display: 'flex', flexDirection: 'column', minWidth: 0 } },
      h('b', {}, [pl.city, pl.state].filter(Boolean).join(', ')), h('span', { class: 'hint' }, `${num(pl.n)} photos`)))));
  const m = L.map(mapBox, { worldCopyJump: true, preferCanvas: true });
  L.tileLayer(TILE_URL, { ...TILE_OPTS, maxZoom: 19, attribution: TILE_ATTR }).addTo(m);
  const cluster = L.markerClusterGroup({
    showCoverageOnHover: false, maxClusterRadius: 60, chunkedLoading: true,
    iconCreateFunction: (c) => {
      const first = c.getAllChildMarkers()[0].options.ep;
      return L.divIcon({ className: 'mk', iconSize: [52, 52], html: `<div class="mk-thumb"><img src="${thumbUrl(first[0], first[3])}" alt=""><b>${c.getChildCount()}</b></div>` });
    },
  });
  const markers = points.map(pt => {
    const mk = L.marker([pt[1], pt[2]], { ep: pt, icon: L.divIcon({ className: 'mk', iconSize: [52, 52], html: `<div class="mk-thumb"><img src="${thumbUrl(pt[0], pt[3])}" alt=""></div>` }) });
    mk.on('click', () => openViewer({ items: [{ id: pt[0], v: pt[3], k: 'i', w: 1, h: 1 }], index: 0 }));
    return mk;
  });
  cluster.addLayers(markers);
  cluster.on('clusterclick', (e) => {
    if (m.getZoom() >= 16) {
      const items = e.layer.getAllChildMarkers().map(mk => ({ id: mk.options.ep[0], v: mk.options.ep[3], k: 'i', w: 1, h: 1 }));
      openViewer({ items, index: 0 });
    }
  });
  m.addLayer(cluster);
  if (q.lat && q.lon) m.setView([+q.lat, +q.lon], 15);
  else if (points.length) m.fitBounds(cluster.getBounds(), { padding: [40, 40], maxZoom: 12 });
  else m.setView([39.8, -86.15], 4);
  setTimeout(() => m.invalidateSize(), 100);
  return () => m.remove();
}

export async function place(el, p, q) {
  el.append(head([q.city, q.state].filter(Boolean).join(', ') || 'Place'));
  const query = { city: q.city };
  if (q.state) query.state = q.state;
  return gridPage(el, query);
}

// ---------------------------------------------------------------- Calendar

export async function calendar(el, p, q) {
  const today = new Date();
  let y = +(q.y || today.getFullYear()), m = +(q.m || today.getMonth() + 1);
  const title = h('h1', {});
  const grid = h('div', { class: 'cal' });
  const memBox = h('div');
  const nav = (d) => { m += d; if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } history.replaceState(null, '', `#/calendar?y=${y}&m=${m}`); draw(); };
  const monthsSel = h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Jump to month', onchange: () => { const [yy, mm] = monthsSel.value.split('-'); y = +yy; m = +mm; draw(); } });
  el.append(h('div', { class: 'page-head' },
    h('button', { class: 'iconbtn', 'aria-label': 'Previous month', onclick: () => nav(-1), style: { border: '1px solid var(--line2)' } }, icon('back')),
    title,
    h('button', { class: 'iconbtn', 'aria-label': 'Next month', onclick: () => nav(1), style: { border: '1px solid var(--line2)' } }, icon('next')),
    h('div', { class: 'actions' }, monthsSel)), memBox,
    h('div', { class: 'cal-head' }, DAYS.map(d => h('span', { style: { padding: '0 6px' } }, d))), grid);
  api('/api/assets/months').then(ms => {
    monthsSel.append(h('option', { value: '' }, 'Months with photos…'));
    ms.forEach(x => { const [yy, mm] = x.month.split('-'); monthsSel.append(h('option', { value: x.month }, `${MONTHS_LONG[+mm - 1]} ${yy} (${num(x.count)})`)); });
  }).catch(() => {});
  api('/api/memories').then(ms => {
    if (!ms.length) return;
    memBox.append(h('div', { class: 'card', style: { flexDirection: 'row', alignItems: 'center', padding: '12px 14px', marginBottom: '16px', flexWrap: 'wrap' } },
      icon('clock'), h('div', { class: 'grow' }, h('b', {}, 'On this day'), h('div', { class: 'hint' }, `${MONTHS_LONG[today.getMonth()]} ${today.getDate()} in ${ms.map(x => x.year).join(', ')}`)),
      h('div', { class: 'row', style: { gap: '6px' } }, ms.slice(0, 4).map(x => h('a', { href: `#/memories/${x.year}` }, img(thumbUrl(x.ids[0]), { style: { width: '64px', height: '48px', borderRadius: '8px', objectFit: 'cover' } }))))));
  }).catch(() => {});
  async function draw() {
    title.textContent = `${MONTHS_LONG[m - 1]} ${y}`;
    grid.innerHTML = '';
    let days = [];
    try { days = await api(`/api/calendar/${y}/${m}`); } catch (e) { errToast(e); }
    const byDay = Object.fromEntries(days.map(d => [d.day, d]));
    const first = new Date(y, m - 1, 1).getDay();
    const count = new Date(y, m, 0).getDate();
    for (let i = 0; i < first; i++) grid.append(h('div', { class: 'd empty' }));
    for (let d = 1; d <= count; d++) {
      const info = byDay[d];
      const isToday = y === today.getFullYear() && m === today.getMonth() + 1 && d === today.getDate();
      const dayStr = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const cell = h(info ? 'a' : 'div', { class: 'd' + (info ? ' has' : '') + (isToday ? ' today' : ''), href: info ? `#/day/${dayStr}` : null, 'aria-label': info ? `${fmtDay(dayStr)}: ${info.count} photos` : null },
        info ? img(thumbUrl(info.cover)) : null,
        h('span', { class: 'num' }, d),
        info ? h('span', { class: 'cnt' }, num(info.count)) : null);
      grid.append(cell);
    }
  }
  draw();
}

export async function day(el, p) {
  el.append(head(fmtDay(p.day)));
  return gridPage(el, { day: p.day });
}

// ---------------------------------------------------------------- Albums & tags

export async function albums(el) {
  const list = await api('/api/albums');
  el.append(head('Albums', null, state.user.is_admin ? [h('button', { class: 'btn primary', onclick: async () => {
    const name = await promptDialog({ title: 'New album', label: 'Album name', confirmLabel: 'Create' });
    if (!name) return;
    try { const a = await api('/api/albums', { method: 'POST', body: { name } }); toast('Album created. Select photos and use “Add to album” to fill it.'); go(`#/albums/${a.id}`); } catch (e) { errToast(e); }
  } }, '+ New album')] : []));
  if (!list.length) { el.append(h('div', { class: 'empty' }, icon('album'), state.user.is_admin ? 'No albums yet. Select photos and choose “Add to album”.' : 'No albums yet.')); return; }
  el.append(h('div', { class: 'albumgrid' }, list.map(a => h('a', { href: `#/albums/${a.id}` },
    h('div', { class: 'cov' }, a.cover ? img(thumbUrl(a.cover)) : null), h('b', {}, a.name), h('span', { class: 'hint' }, `${num(a.n)} photos`)))));
}

export async function album(el, p) {
  const list = await api('/api/albums');
  const a = list.find(x => String(x.id) === String(p.id));
  if (!a) { el.append(h('div', { class: 'empty' }, 'Album not found.')); return; }
  const acts = state.user.is_admin ? [
    h('button', { class: 'btn', onclick: async () => {
      const name = await promptDialog({ title: 'Rename album', label: 'Album name', value: a.name });
      if (!name) return;
      try { await api(`/api/albums/${a.id}`, { method: 'PATCH', body: { name } }); go(`#/albums/${a.id}`); } catch (e) { errToast(e); }
    } }, icon('edit'), 'Rename'),
    h('button', { class: 'btn danger', onclick: async () => {
      if (!await confirmDialog({ title: 'Delete this album?', text: 'Only the album is deleted. The photos stay in your library.', confirmLabel: 'Delete album', danger: true })) return;
      try { await api(`/api/albums/${a.id}`, { method: 'DELETE' }); go('#/albums'); } catch (e) { errToast(e); }
    } }, 'Delete album')] : [];
  el.append(head(a.name, `${num(a.n)} photos`, acts));
  return gridPage(el, { album: a.id });
}

export async function tags(el) {
  const list = await api('/api/tags');
  el.append(head('Tags', 'Only tags that were added by hand. Nothing is tagged automatically.'));
  if (!list.length) { el.append(h('div', { class: 'empty' }, icon('tag'), state.user.is_admin ? 'No tags yet. Add tags when editing a photo, or select photos and choose “Add tag”.' : 'No tags yet.')); return; }
  let sort = 'count';
  const box = h('div', { class: 'albumgrid' });
  const draw = () => {
    box.innerHTML = '';
    const items = [...list].sort((a, b) => sort === 'az' ? a.name.localeCompare(b.name) : b.n - a.n);
    items.forEach(t => box.append(h('a', { href: `#/tags/${t.id}` }, h('div', { class: 'cov' }, img(thumbUrl(t.cover))), h('b', {}, t.name), h('span', { class: 'hint' }, `${num(t.n)} photos`))));
  };
  el.append(h('div', { class: 'row', style: { marginBottom: '16px' } },
    h('button', { class: 'chip on', onclick: (e) => { sort = 'count'; e.target.parentNode.querySelectorAll('.chip').forEach(c => c.classList.remove('on')); e.target.classList.add('on'); draw(); } }, 'Most photos'),
    h('button', { class: 'chip', onclick: (e) => { sort = 'az'; e.target.parentNode.querySelectorAll('.chip').forEach(c => c.classList.remove('on')); e.target.classList.add('on'); draw(); } }, 'A to Z')), box);
  draw();
}

export async function tag(el, p) {
  const list = await api('/api/tags');
  const t = list.find(x => String(x.id) === String(p.id)) || { id: p.id, name: 'Tag', n: 0 };
  const acts = state.user.is_admin ? [
    h('button', { class: 'btn', onclick: async () => {
      const name = await promptDialog({ title: 'Rename tag', label: 'Tag name', value: t.name });
      if (!name) return;
      try { const r = await api(`/api/tags/${t.id}`, { method: 'PATCH', body: { name } }); go(`#/tags/${r.id}`); } catch (e) { errToast(e); }
    } }, icon('edit'), 'Rename tag'),
    h('button', { class: 'btn danger', onclick: async () => {
      if (!await confirmDialog({ title: `Remove “${t.name}” from all photos?`, text: 'The photos themselves are not touched.', confirmLabel: 'Remove tag', danger: true })) return;
      try { await api(`/api/tags/${t.id}`, { method: 'DELETE' }); go('#/tags'); } catch (e) { errToast(e); }
    } }, 'Remove tag from all')] : [];
  el.append(head(t.name, `${num(t.n)} photos · newest first`, acts));
  return gridPage(el, { tag: t.id });
}

export async function favorites(el) {
  el.append(head('Favorites', 'Your own favorites. Other people don’t see these.'));
  return gridPage(el, { favorites: 1 }, { emptyText: 'Tap the heart on a photo to add it here.' });
}

export async function search(el, p, q) {
  const text = q.q || '';
  el.append(head(`Results for “${text}”`));
  // Smart results: photos that look like the words, best match first
  const smart = h('section', { class: 'smart-results' });
  const words = h('section', {});
  el.append(smart, words);
  let items = [], offset = 0, total = 0;
  const tiles = h('div', { class: 'smart-grid' });
  const more = h('div', { style: { textAlign: 'center', padding: '12px' } });
  async function loadSmart() {
    let r;
    try { r = await api(`/api/search/smart?q=${encodeURIComponent(text)}&offset=${offset}&limit=120`); } catch (e) { return; }
    if (!r.enabled) return;
    if (!offset) {
      smart.innerHTML = '';
      total = r.total;
      smart.append(h('h3', { class: 'section-title' }, 'Looks like “', text, '”', h('span', { class: 'hint', style: { fontWeight: 400, marginLeft: '8px' } }, total ? `${num(total)} · best matches first` : '')));
      if (r.left) smart.append(h('div', { class: 'hint', style: { marginBottom: '8px' } }, `Smart search is still looking at your photos (${num(r.left)} to go), so more may show up later.`));
      if (!total) smart.append(h('div', { class: 'hint', style: { padding: '6px 0 14px' } }, r.ready ? 'No photos look like that.' : 'Smart search isn’t ready yet. It starts after faces are found.'));
      smart.append(tiles, more);
    }
    const start = items.length;
    items.push(...r.items);
    r.items.forEach((it, i) => tiles.append(h('button', { class: 'smart-tile', 'aria-label': 'Open photo', onclick: () => openViewer({ items, index: start + i }) },
      h('img', { src: thumbUrl(it.id, it.v), alt: '', loading: 'lazy', decoding: 'async' }),
      it.k === 'v' ? h('span', { class: 'dur' }, icon('play')) : null)));
    offset += r.items.length;
    more.innerHTML = '';
    if (offset < total) more.append(h('button', { class: 'btn small', onclick: loadSmart }, `Show more (${num(total - offset)} left)`));
  }
  loadSmart();
  words.append(h('h3', { class: 'section-title', style: { marginTop: '18px' } }, 'Matching names, places, tags and file names'));
  return gridPage(words, { q: text }, { emptyText: 'Nothing with that name, place, tag or file name.' });
}

export async function more(el) {
  const u = state.user;
  const link = (hash, ic, label) => h('a', { class: 'card', href: hash, style: { flexDirection: 'row', alignItems: 'center', padding: '14px 16px', color: 'var(--text)' } }, icon(ic), h('b', { class: 'grow' }, label), icon('next'));
  el.append(head('More'), h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
    link('#/albums', 'album', 'Albums'), link('#/tags', 'tag', 'Tags'), link('#/favorites', 'heart', 'Favorites'),
    u.is_admin ? [
      h('div', { class: 'label hint', style: { marginTop: '10px' } }, 'LIBRARY'),
      link('#/admin/library', 'folder', 'Folders & scanning'), link('#/admin/faces', 'scan', 'Faces'), link('#/admin/duplicates', 'dup', 'Duplicates'), link('#/admin/quality', 'blur', 'Blurry photos'), link('#/admin/empty-folders', 'folder', 'Empty folders'), link('#/admin/other-files', 'doc', 'Non-photo files'),
      link('#/admin/hidden', 'eyeoff', 'Hidden'), link('#/admin/recycle', 'trash', 'Recycle Bin'),
      h('div', { class: 'label hint', style: { marginTop: '10px' } }, 'ADMIN'),
      link('#/admin/users', 'users', 'Users'), link('#/admin/apikeys', 'key', 'API keys'), link('#/admin/settings', 'settings', 'Settings'), link('#/admin/logs', 'file', 'Logs'),
    ] : null));
}

// Lets the admin pick which face is shown as this person's picture.
export async function chooseCover(personId, name, after) {
  const grid = h('div', { class: 'facegrid' });
  // the box scrolls, not the grid itself (a grid with a height limit gets squashed in some browsers)
  const more = h('div', { style: { textAlign: 'center', padding: '12px 0 4px' } });
  const scroller = h('div', { class: 'facegrid-scroll' }, grid, more);
  let offset = 0;
  let m;
  async function load() {
    const faces = await api(`/api/people/${personId}/faces?offset=${offset}&limit=120`);
    offset += faces.length;
    faces.forEach(f => {
      const tile = h('button', { class: 'f', style: { border: 0, padding: 0 }, 'aria-label': 'Use this picture', onclick: async () => {
        try {
          await api(`/api/people/${personId}`, { method: 'PATCH', body: { cover_face_id: f.id } });
          toast('Picture updated.');
          m.close();
          after && after();
        } catch (e) { errToast(e); }
      } }, h('img', { src: faceUrl(f.id), alt: '', loading: 'lazy', decoding: 'async' }));
      grid.append(tile);
    });
    more.innerHTML = '';
    if (faces.length === 120) more.append(h('button', { class: 'btn small', onclick: load }, 'Show more'));
  }
  m = modal({
    title: `Choose a picture for ${name || 'this person'}`, wide: true,
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      h('span', { class: 'hint' }, 'Tap the face you want to show on the People page. The clearest faces are first.'), scroller),
  });
  load().catch(errToast);
}
