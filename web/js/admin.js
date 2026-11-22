// Admin pages.
import { h, icon, api, nameSafely, thumbUrl, faceUrl, img, toast, errToast, modal, confirmDialog, promptDialog, fmtBytes, fmtAgo, fmtEta, num, fmtDateTime } from './ui.js';
import { PhotoGrid } from './grid.js';
import { state } from './state.js';
import { go } from './router.js';
import { openViewer } from './viewer.js';

function head(title, sub = null, actions = []) {
  return h('div', { class: 'page-head' },
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: '4px' } }, h('h1', {}, title), sub ? h('span', { class: 'sub' }, sub) : null),
    actions.length ? h('div', { class: 'actions' }, actions) : null);
}

const post = (url, body = {}) => api(url, { method: 'POST', body });

// ---------------------------------------------------------------- Library

const expanded = new Set(); // folders the admin opened (kept while browsing)

export async function library(el) {
  const progressBox = h('div');
  const activityBox = h('div');
  const libsBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '20px' } });
  const scanBtn = h('button', { class: 'btn', onclick: async () => {
    try {
      if (scanBtn.dataset.mode === 'stop') { await post('/api/admin/jobs/stop_scan'); toast('Stopping the scan. Thumbnails keep going for what was found.'); }
      else { await post('/api/admin/jobs/scan'); toast('Looking for photos…'); }
      setTimeout(tick, 800);
    } catch (e) { errToast(e); }
  } }, 'Scan for new photos');
  el.append(head('Library', 'Your photo folders. EasyPhotos reads them; it only changes files when you delete something.', [
    scanBtn, h('button', { class: 'btn primary', onclick: () => findFaces() }, icon('scan'), 'Find faces'),
  ]), progressBox, activityBox, libsBox);

  async function findFaces() {
    try { await post('/api/admin/jobs/find_faces'); toast('Finding faces in every photo. This runs in the background at low priority.'); tick(); }
    catch (e) { errToast(e); }
  }

  // ------------------------------------------------ what's happening (3 steps)
  function step(n, title, state, lines) {
    const mark = state === 'done' ? h('span', { class: 'stepmark done' }, icon('check'))
      : h('span', { class: 'stepmark' + (state === 'busy' ? ' busy' : state === 'warn' ? ' warn' : '') }, String(n));
    return h('div', { class: 'step' }, mark, h('div', { class: 'grow', style: { display: 'flex', flexDirection: 'column', gap: '8px', minWidth: 0 } },
      h('b', {}, title), lines));
  }

  function drawProgress(j, libs) {
    const c = j.counts;
    const scanning = j.scanning || [];
    const stopped = libs.filter(l => l.scan_stopped);
    scanBtn.dataset.mode = scanning.length ? 'stop' : '';
    scanBtn.textContent = scanning.length ? 'Stop scan' : stopped.length ? 'Resume scan' : 'Scan for new photos';
    scanBtn.className = scanning.length ? 'btn warn' : 'btn';
    progressBox.innerHTML = '';
    const card = h('section', { class: 'card', style: { marginBottom: '20px' } }, h('h2', {}, 'What’s happening'));
    if (!j.worker_alive) card.append(h('div', { class: 'hint', style: { color: 'var(--orange)' } }, 'The background worker hasn’t checked in for a few minutes. If this stays, look at Logs.'));
    if (j.paused) card.append(h('div', { class: 'row', style: { color: 'var(--orange)' } }, 'All background work is paused.',
      h('button', { class: 'btn small', onclick: async () => { await post('/api/admin/jobs/resume'); tick(); } }, 'Resume')));

    // Step 1: finding photos
    const sc = j.status.scan;
    let s1;
    if (scanning.length) {
      s1 = step(1, 'Finding your photos', 'busy', [
        ...scanning.map(s => h('div', {}, `Looking through ${s.name}… `, h('b', {}, num(s.found)), ' photos & videos found so far (still counting)')),
        h('div', { class: 'hint' }, 'Thumbnails are made at the same time. You can stop the scan any time; resuming skips everything already found.'),
      ]);
    } else if (stopped.length) {
      s1 = step(1, 'Finding your photos (stopped)', 'warn', [h('div', {}, stopped.map(l => l.scan_message).join(' ')),
        h('div', {}, h('button', { class: 'btn small', onclick: async () => { await post('/api/admin/jobs/scan'); toast('Resuming. Files already found are skipped.'); setTimeout(tick, 800); } }, 'Resume scan'))]);
    } else if (!libs.length) {
      s1 = step(1, 'Finding your photos', 'todo', [h('div', { class: 'hint' }, 'Add a library below to start.')]);
    } else {
      const err = sc && sc.state === 'error';
      s1 = step(1, `Found ${num(c.total)} photos & videos`, err ? 'warn' : 'done', [
        sc ? h('div', { class: 'hint', style: err ? { color: 'var(--orange)' } : null }, `Last scan ${fmtAgo(sc.updated_at).toLowerCase()}: ${sc.message || ''}`) : null,
        err ? h('a', { href: '#/admin/logs?problems=1' }, 'See what went wrong →') : null]);
    }
    card.append(s1);

    // Step 2: thumbnails
    const done = Math.max(0, c.total - c.meta_pending);
    const pct = c.total ? Math.floor(done / c.total * 100) : 0;
    const thumbLines = [
      h('div', {}, h('b', {}, num(done)), ` of ${num(c.total)} done`, c.meta_pending && j.thumbs_eta_seconds ? h('span', { class: 'hint' }, ` · ${fmtEta(j.thumbs_eta_seconds)}${scanning.length ? ' (for what’s found so far)' : ''}`) : null,
        j.thumbs_rate && c.meta_pending ? h('span', { class: 'hint' }, ` · about ${num(Math.round(j.thumbs_rate))} per minute`) : null),
      h('div', { class: 'progress' }, h('div', { style: { width: `${pct}%` } })),
    ];
    if (c.meta_errors) thumbLines.push(h('div', { class: 'hint', style: { color: 'var(--orange)' } }, `${num(c.meta_errors)} files couldn’t be read (damaged or unsupported). `, h('a', { href: '#/admin/logs?problems=1' }, 'See which')));
    card.append(step(2, 'Making thumbnails', !c.total ? 'todo' : c.meta_pending ? 'busy' : 'done', thumbLines));

    // Step 3: faces
    const facesDone = c.images ? Math.max(0, c.images - c.faces_pending) : 0;
    if (j.faces_started) {
      const fpct = c.images ? Math.floor(facesDone / c.images * 100) : 0;
      card.append(step(3, 'Finding faces', c.faces_pending ? 'busy' : 'done', [
        h('div', {}, h('b', {}, num(facesDone)), ` of ${num(c.images)} photos checked`, j.faces_eta_seconds && c.faces_pending ? h('span', { class: 'hint' }, ` · ${fmtEta(j.faces_eta_seconds)}`) : null),
        h('div', { class: 'progress' }, h('div', { style: { width: `${fpct}%` } })),
        h('div', { class: 'row', style: { fontSize: '13px', color: 'var(--text2)', gap: '6px 24px' } },
          h('span', {}, `${num(j.faces_found)} faces found`), h('span', {}, `grouped into ${num(j.people_groups)} people`), h('a', { href: '#/admin/faces' }, 'Name them →')),
        c.meta_pending ? h('div', { class: 'hint' }, 'Faces are checked after each photo’s thumbnail is made.') : null,
      ]));
    } else {
      card.append(step(3, 'Finding faces', 'todo', [
        h('div', { class: 'hint' }, 'Not started. Click “Find faces” to look through every photo. It can take a day or more on a big library and won’t slow down your other apps.'),
        h('div', {}, h('button', { class: 'btn small primary', onclick: findFaces }, icon('scan'), 'Find faces'))]));
    }
    const chip = (text) => h('span', { class: 'badge green', style: { padding: '6px 12px', fontWeight: 500, fontSize: '13px' } }, text);
    card.append(h('div', { class: 'row', style: { paddingTop: '12px', borderTop: '1px solid var(--line)', gap: '10px' } },
      chip(`Uses ${j.threads} of ${j.cpus} threads at lowest priority`),
      j.night_boost ? chip(`${j.night_threads} threads overnight`) : null,
      chip('Other apps always go first'),
      j.paused ? null : h('button', { class: 'btn small ghost', onclick: async () => { await post('/api/admin/jobs/pause'); toast('Paused. Everything picks up where it left off when you resume.'); tick(); } }, 'Pause everything'),
      h('a', { href: '#/admin/settings', style: { fontSize: '13px' } }, 'Change speed')));
    progressBox.append(card);
  }

  // ------------------------------------------------ live activity
  let onlyProblems = false, lastId = 0, items = [];
  const list = h('div', { class: 'activity' });
  const probToggle = h('input', { type: 'checkbox', onchange: () => { onlyProblems = probToggle.checked; lastId = 0; items = []; loadActivity(); } });
  activityBox.append(h('section', { class: 'card', style: { marginBottom: '20px', gap: '10px' } },
    h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Live activity'),
      h('label', { class: 'check', style: { minHeight: 0, fontSize: '13px' } }, probToggle, 'Only problems'),
      h('a', { href: '#/admin/logs', style: { fontSize: '13px' } }, 'Full logs →')),
    list));
  async function loadActivity() {
    try {
      const rows = await api(`/api/admin/activity?limit=40&problems=${onlyProblems ? 1 : 0}&after=${lastId}`);
      if (rows.length) { lastId = rows[0].id; items = [...rows, ...items].slice(0, 40); }
      list.innerHTML = '';
      if (!items.length) list.append(h('div', { class: 'hint' }, onlyProblems ? 'No problems so far.' : 'Nothing yet. Activity shows up here while EasyPhotos works.'));
      for (const r of items) {
        const t = new Date(r.ts * 1000);
        list.append(h('div', { class: 'act-row ' + r.level },
          h('span', { class: 'mono muted' }, t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })),
          h('span', { class: 'act-kind' }, { cleanup: 'Clean-up', scan: 'Scan', thumbs: 'Thumbnail', faces: 'Faces', duplicates: 'Duplicates', command: 'You' }[r.kind] || r.kind),
          h('span', { class: 'act-text' }, r.text)));
      }
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------ libraries with collapsible folders
  const folderCache = {};
  function drawLibs(libs, jobs) {
    libsBox.innerHTML = '';
    if (!libs.length) {
      libsBox.append(h('div', { class: 'card' }, h('h2', {}, 'Add your first library'),
        h('p', { class: 'hint', style: { margin: 0 } }, 'In Unraid, map your photo share into the container (for example /mnt/user/Photos → /libraries/family). Then add it here.'),
        h('div', {}, h('button', { class: 'btn primary', onclick: () => addLibrary(tick) }, '+ Add library'))));
      return;
    }
    for (const lib of libs) {
      const treeBox = h('div');
      const status = lib.scanning ? h('span', { class: 'hint', style: { color: 'var(--accent2)' } }, `Scanning now… ${num(lib.scan_found)} found`)
        : h('span', { class: 'hint' }, `${num(lib.count)} files · scanned ${fmtAgo(lib.last_scan_at).toLowerCase()}`);
      libsBox.append(h('section', { class: 'card', style: { padding: '0 0 8px', gap: '0' } },
        h('div', { class: 'row', style: { padding: '16px 20px' } },
          icon('folder'),
          h('div', { class: 'grow', style: { minWidth: 0 } }, h('b', { style: { fontSize: '16px' } }, lib.name), lib.enabled ? null : h('span', { class: 'badge', style: { marginLeft: '8px' } }, 'Turned off'),
            h('div', { class: 'mono muted' }, lib.path), status,
            lib.scan_message && !lib.scanning ? h('div', { class: 'hint', style: lib.scan_stopped || /couldn|empty|not found/.test(lib.scan_message) ? { color: 'var(--orange)' } : null }, lib.scan_message) : null),
          !lib.exists ? h('span', { class: 'badge orange' }, 'Folder missing') : null,
          lib.exists && !lib.writable ? h('span', { class: 'badge', title: 'Deleting needs Read/Write' }, 'Read-only') : null,
          lib.scanning ? h('button', { class: 'btn small warn', onclick: async () => { await post('/api/admin/jobs/stop_scan'); toast('Stopping…'); setTimeout(tick, 800); } }, 'Stop scan')
            : h('button', { class: 'btn small', onclick: async () => { await post('/api/admin/jobs/scan', { library_id: lib.id }); toast(lib.scan_stopped ? 'Resuming. Files already found are skipped.' : 'Scanning…'); setTimeout(tick, 800); } }, lib.scan_stopped ? 'Resume scan' : 'Scan'),
          h('button', { class: 'iconbtn', 'aria-label': 'Library options', onclick: () => libraryOptions(lib, tick) }, icon('more'))),
        h('div', { class: 'folder-row head' }, h('span', {}, 'Folder'), h('span', {}, 'Files'), h('span', {}, 'Faces'), h('span', {}, 'Status')),
        treeBox));
      const draw = (folders) => drawTree(treeBox, lib, folders, jobs);
      if (folderCache[lib.id]) draw(folderCache[lib.id]);
      api(`/api/admin/libraries/${lib.id}/folders`).then(f => { folderCache[lib.id] = f; draw(f); }).catch(errToast);
    }
    libsBox.append(h('div', {}, h('button', { class: 'btn', onclick: () => addLibrary(tick) }, '+ Add another library')));
  }

  function drawTree(box, lib, folders, jobs) {
    const byPath = {}, kids = {};
    folders.forEach(f => { byPath[f.path] = f; });
    folders.forEach(f => {
      if (!f.path) return;
      const parent = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : '';
      (kids[parent] = kids[parent] || []).push(f);
    });
    box.innerHTML = '';
    const facesOn = jobs && jobs.faces_started;
    const statusOf = (f) => {
      if (f.pending) return [`Thumbnails ${num(f.count - f.pending)}/${num(f.count)}`, 'var(--accent2)'];
      if (!facesOn) return ['Thumbnails done', 'var(--muted)'];
      if (f.faces_done >= f.count) return ['All done', 'var(--green)'];
      return [`Faces ${num(f.faces_done)}/${num(f.count)}`, 'var(--accent2)'];
    };
    const render = (parentPath, depth) => {
      const list = (kids[parentPath] || []).sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }));
      for (const f of list) {
        const key = `${lib.id}|${f.path}`;
        const hasKids = !!(kids[f.path] && kids[f.path].length);
        const open = expanded.has(key);
        const st = statusOf(f);
        const toggle = h('button', { class: 'tree-toggle', 'aria-label': open ? 'Collapse' : 'Expand', 'aria-expanded': open ? 'true' : 'false',
          style: { visibility: hasKids ? 'visible' : 'hidden' }, onclick: (e) => { e.preventDefault(); e.stopPropagation(); open ? expanded.delete(key) : expanded.add(key); drawTree(box, lib, folders, jobs); } },
          icon(open ? 'down2' : 'next'));
        box.append(h('div', { class: 'folder-row' },
          h('span', { style: { display: 'flex', alignItems: 'center', gap: '6px', paddingLeft: `${depth * 20}px`, minWidth: 0 } }, toggle,
            h('a', { href: `#/admin/folder?library=${lib.id}&folder=${encodeURIComponent(f.path)}`, style: { display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text)', minWidth: 0 } },
              icon('folder'), h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: f.path }, f.path.split('/').pop()))),
          h('span', { class: 'muted' }, num(f.count)), h('span', { class: 'muted' }, num(f.faces)),
          h('span', { style: { color: st[1], fontSize: '13px' } }, st[0])));
        if (open && hasKids) render(f.path, depth + 1);
      }
    };
    const root = byPath[''];
    if (root && (kids[''] || []).length === 0) {
      box.append(h('div', { class: 'hint', style: { padding: '10px 20px' } }, `${num(root.count)} files directly in this folder.`));
    }
    render('', 0);
    if (!folders.length) box.append(h('div', { class: 'hint', style: { padding: '10px 20px' } }, 'No photos found yet.'));
  }

  // ------------------------------------------------ refresh loop
  let lastJobs = null, ticks = 0;
  async function tick() {
    try {
      const [j, libs] = await Promise.all([api('/api/admin/jobs'), api('/api/admin/libraries')]);
      lastJobs = j;
      drawProgress(j, libs);
      const busy = (j.scanning && j.scanning.length) || j.counts.meta_pending || (j.faces_started && j.counts.faces_pending);
      if (ticks++ === 0 || busy || ticks % 6 === 0) drawLibs(libs, j);
    } catch (e) { /* ignore */ }
    loadActivity();
  }
  await tick();
  const t = setInterval(tick, 4000);
  void lastJobs;
  return () => clearInterval(t);
}

function libraryOptions(lib, after) {
  const name = h('input', { class: 'input', value: lib.name });
  const enabled = h('input', { type: 'checkbox', checked: lib.enabled });
  modal({
    title: 'Library options',
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      h('label', { class: 'field' }, 'Name', name),
      h('label', { class: 'check' }, enabled, 'Show this library (turn off to hide it from everyone)'),
      h('div', { class: 'mono muted' }, lib.path)),
    actions: [
      { label: 'Remove library', cls: 'danger', run: async () => {
        if (!await confirmDialog({ title: `Remove “${lib.name}” from EasyPhotos?`, text: 'Your files are NOT deleted. EasyPhotos just forgets this folder, including its thumbnails, faces and tags.', confirmLabel: 'Remove', danger: true, typeWord: 'REMOVE' })) return true;
        await api(`/api/admin/libraries/${lib.id}`, { method: 'DELETE' });
        toast('Library removed.'); after();
      } },
      { label: 'Cancel', cls: 'ghost' },
      { label: 'Save', cls: 'primary', run: async () => {
        await api(`/api/admin/libraries/${lib.id}`, { method: 'PATCH', body: { name: name.value, enabled: enabled.checked } });
        after();
      } },
    ],
  });
}

async function addLibrary(after) {
  const name = h('input', { class: 'input', placeholder: 'e.g. Family Photos' });
  const path = h('input', { class: 'input mono', placeholder: '/libraries/family' });
  const list = h('div', { class: 'ftree' });
  const crumbs = h('div', { class: 'mono muted' });
  async function browse(p) {
    try {
      const r = await api('/api/admin/browse?path=' + encodeURIComponent(p || ''));
      crumbs.textContent = r.path;
      path.value = r.path;
      list.innerHTML = '';
      if (r.parent) list.append(h('label', { style: { cursor: 'pointer' }, onclick: () => browse(r.parent) }, icon('back'), '..'));
      r.dirs.forEach(d => list.append(h('label', { style: { cursor: 'pointer' }, onclick: () => browse((r.path === '/' ? '' : r.path) + '/' + d) }, icon('folder'), d)));
      if (!r.dirs.length) list.append(h('div', { class: 'hint', style: { padding: '8px' } }, 'No folders inside.'));
    } catch (e) { errToast(e); }
  }
  modal({
    title: 'Add a library', wide: true,
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      h('label', { class: 'field' }, 'Name', name),
      h('label', { class: 'field' }, 'Folder inside the container', h('span', { class: 'hint' }, 'Pick a folder below or type it. It must be mapped in the Unraid container settings.'), path),
      crumbs, list),
    actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Add library', cls: 'primary', run: async () => {
      await post('/api/admin/libraries', { name: name.value || path.value.split('/').pop(), path: path.value });
      toast('Library added. Scanning has started.');
      after();
    } }],
  });
  browse('');
}

export async function folder(el, p, q) {
  const libs = await api('/api/admin/libraries');
  const lib = libs.find(l => String(l.id) === q.library);
  el.append(head(q.folder ? q.folder.split('/').pop() : (lib ? lib.name : 'Folder'), `${lib ? lib.name : ''}${q.folder ? ' / ' + q.folder : ''}`,
    [h('a', { class: 'btn', href: '#/admin/library' }, icon('back'), 'Back to library')]));
  const g = new PhotoGrid({ query: { library: q.library, folder: q.folder || '' } });
  g.mount(el);
  return () => g.destroy();
}

// ---------------------------------------------------------------- Faces

// Where you were on the Faces list, so coming back from a group or a photo
// puts you in the same spot instead of the top.
let facesSpot = null;

export async function faces(el, p, q) {
  let tab = q.tab || 'needs', sort = 'count', offset = 0;
  const back = facesSpot && facesSpot.tab === tab && Date.now() - facesSpot.at < 6 * 3600e3 ? facesSpot : null;
  facesSpot = null;
  if (back) sort = back.sort;
  const tabs = h('div', { class: 'tabs' });
  const grid = h('div', { class: 'fgrid' });
  const more = h('div', { style: { textAlign: 'center', padding: '16px' } });
  const sortSel = h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Sort', onchange: (e) => { sort = e.target.value; load(true); } },
    h('option', { value: 'count' }, 'Most photos first'), h('option', { value: 'new' }, 'Newest first'));
  sortSel.value = sort;
  const intro = h('p', { class: 'hint', style: { margin: '0 0 12px' } });
  el.append(head('Library › Faces', 'Each card is one person the AI grouped together. Type a name, or leave it unmatched. Click a card to fix wrong faces in it.', [sortSel]),
    tabs, h('div', { style: { height: '12px' } }), intro, grid, more);
  const names = { needs: 'Needs a name', possible: 'Possible matches', named: 'Named', unmatched: 'Left unmatched', single: 'Single faces', hidden: 'Hidden' };
  const remember = () => { facesSpot = { tab, sort, count: grid.querySelectorAll('.fcard').length, y: window.scrollY, at: Date.now() }; };
  async function load(reset = false, want = 40) {
    if (reset) { offset = 0; grid.innerHTML = ''; }
    let r;
    try { r = await api(`/api/admin/faces?tab=${tab}&sort=${sort}&offset=${offset}&limit=${want}`); } catch (e) { return errToast(e); }
    tabs.innerHTML = '';
    Object.entries(names).forEach(([k, label]) => tabs.append(h('button', { class: tab === k ? 'on' : '', onclick: () => { tab = k; history.replaceState(null, '', `#/admin/faces?tab=${k}`); load(true); } }, `${label} · ${num(r.counts[k])}`)));
    sortSel.classList.toggle('hidden', tab === 'possible');
    intro.innerHTML = '';
    if (r.favorites_only) {
      intro.append(h('div', { class: 'card', style: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: '8px', borderColor: 'var(--accent)', marginBottom: '8px' } },
        icon('star'),
        h('span', { class: 'grow' }, r.favorites
          ? `Only looking for your ${num(r.favorites)} favorite${r.favorites > 1 ? 's' : ''}. ${r.hidden_by_favorites ? `${num(r.hidden_by_favorites)} other groups are tucked away.` : ''}`
          : 'Only looking for favorites, but nobody is starred yet. Star people in the Named tab.'),
        h('button', { class: 'btn small', onclick: async () => { try { await api('/api/admin/settings', { method: 'PUT', body: { faces_favorites_only: false } }); toast('Looking for everyone again.'); load(true); } catch (e) { errToast(e); } } }, 'Show everyone')));
    }
    if (tab === 'possible') intro.append(h('div', {}, r.favorites_only ? 'Groups that look like one of your favorites, best matches first. Tap “Yes” if it’s them, or “Not …” if it isn’t.' : 'Groups that look like someone you already named, best matches first. Tap “Yes” if it’s them, or “Not …” if it isn’t and it will stop suggesting that person.'));
    intro.classList.toggle('hidden', !intro.childNodes.length);
    if (!r.items.length && !offset) {
      grid.append(h('div', { class: 'empty', style: { gridColumn: '1/-1' } }, icon('scan'),
        tab === 'needs' ? 'Nothing waiting for a name. If face finding is still running, more will show up here.'
          : tab === 'possible' ? (r.counts.named ? 'No groups look like anyone you’ve named right now.' : 'Name a few people first. Groups that look like them will show up here.') : 'Nothing here.'));
    }
    r.items.forEach(item => grid.append(card(item)));
    offset += r.items.length;
    more.innerHTML = '';
    if (r.items.length === want) more.append(h('button', { class: 'btn', onclick: () => load() }, 'Show more'));
  }
  function card(item) {
    const sug = item.suggest && !item.name ? item.suggest : null;
    const input = h('input', { class: 'input', placeholder: sug ? 'Or type a different name…' : 'Type a name…', value: item.name || '', list: 'people-names', 'aria-label': 'Name' });
    const c = h('article', { class: 'fcard' });
    const done = (msg) => { toast(msg); if (tab !== 'named') c.remove(); };
    const save = async () => {
      const name = input.value.trim();
      if (!name) { toast('Type a name first.', true); return; }
      try {
        const r = await nameSafely(name, (extra) => api(`/api/people/${item.id}`, { method: 'PATCH', body: { name, ...extra } }));
        if (!r) return;
        done(r.merged ? `Added to ${name}.` : `Saved as ${name}.`);
      } catch (e) { errToast(e); }
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
    const matchWord = { strong: 'Strong match', good: 'Good match', maybe: 'Maybe' };
    const faceCount = item.count || 1;
    const trashAll = async (e) => {
      e.stopPropagation();
      if (faceCount > 1 && !await confirmDialog({ title: `Stop asking about this person?`, text: `Removes all ${num(faceCount)} of their faces from face matching. The photos stay. You can undo right after.`, confirmLabel: 'Remove faces', danger: true })) return;
      try {
        const r = await post(`/api/people/${item.id}/ignore`);
        const at = c.nextSibling, parent = c.parentNode;
        c.remove();
        toast(faceCount > 1 ? `Removed ${num(r.face_ids.length)} faces. Photos untouched.` : 'Face removed. The photo is untouched.', false, { label: 'Undo', run: async () => {
          try { await post('/api/faces/restore', { ids: r.face_ids, person_id: r.person_id }); parent.insertBefore(c, at); toast('Brought back.'); } catch (err) { errToast(err); }
        } });
      } catch (err) { errToast(err); }
    };
    const trashOne = async (e, fid, wrap) => {
      e.stopPropagation();
      try {
        await api(`/api/faces/${fid}`, { method: 'PATCH', body: { reject: true } });
        wrap.classList.add('gone');
        toast('Face removed. The photo is untouched.', false, { label: 'Undo', run: async () => {
          try { await post('/api/faces/restore', { ids: [fid], person_id: item.id }); wrap.classList.remove('gone'); } catch (err) { errToast(err); }
        } });
      } catch (err) { errToast(err); }
    };
    const trashBtn = (label, fn) => h('button', { class: 'face-trash', title: label, 'aria-label': label, onclick: fn }, icon('trash'));
    c.append(
      h('div', { class: 'top', onclick: () => { remember(); go(`#/admin/faces/${item.id}`); } },
        h('div', { class: 'cover-wrap' },
          item.cover ? h('img', { class: 'cover', src: faceUrl(item.cover), alt: '', loading: 'lazy', decoding: 'async', width: 72, height: 72 }) : h('div', { class: 'cover' }),
          !item.name ? trashBtn(faceCount > 1 ? 'Remove all of this person’s faces' : 'Remove this face', trashAll) : null),
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 } },
          h('b', {}, item.name || `${num(item.photos)} photo${item.photos === 1 ? '' : 's'}`),
          h('span', { class: 'hint' }, item.name ? `${num(item.photos)} photos` : item.years))),
      item.samples.length ? h('div', { class: 'samples' }, item.samples.map(s => {
        const wrap = h('div', { class: 'sample' }, h('img', { src: faceUrl(s), alt: '', loading: 'lazy', decoding: 'async' }));
        if (!item.name) wrap.append(trashBtn('Remove just this face', (e) => trashOne(e, s, wrap)));
        return wrap;
      })) : null,
      sug ? h('div', { class: 'suggest-box' },
        h('div', {}, 'Looks like ', h('b', {}, sug.name), '?', h('span', { class: 'badge ' + (sug.match === 'strong' ? 'green' : '') , style: { marginLeft: '8px' } }, matchWord[sug.match] || '')),
        h('div', { class: 'row', style: { gap: '6px', flexWrap: 'nowrap' } },
          h('button', { class: 'btn small primary grow', onclick: async () => {
            try { await api(`/api/people/${item.id}`, { method: 'PATCH', body: { name: sug.name, person_id: sug.id } }); done(`Added to ${sug.name}.`); } catch (e) { errToast(e); }
          } }, `Yes, it’s ${sug.name}`),
          h('button', { class: 'btn small grow', onclick: async () => {
            try {
              const r = await api(`/api/people/${item.id}`, { method: 'PATCH', body: { not_person: sug.id } });
              toast(`Got it, not ${sug.name}.`);
              if (tab === 'possible' && !r.suggest) { c.remove(); return; }
              c.replaceWith(card({ ...item, suggest: r.suggest }));
            } catch (e) { errToast(e); }
          } }, `Not ${sug.name}`))) : null,
      input,
      h('div', { class: 'row', style: { gap: '6px', flexWrap: 'nowrap' } },
        item.name ? (() => {
          const b = h('button', { class: 'btn small' + (item.favorite ? ' primary' : ''), title: 'Favorite', 'aria-label': 'Favorite', onclick: async () => {
            try {
              await api(`/api/people/${item.id}`, { method: 'PATCH', body: { favorite: !item.favorite } });
              item.favorite = !item.favorite; b.classList.toggle('primary', item.favorite);
              toast(item.favorite ? `${item.name} is a favorite.` : `${item.name} is no longer a favorite.`);
            } catch (e) { errToast(e); }
          } }, icon('star'));
          return b;
        })() : null,
        h('button', { class: 'btn small grow' + (sug ? '' : ' primary'), onclick: save }, item.name ? 'Rename' : 'Save name'),
        tab === 'unmatched' || tab === 'hidden'
          ? h('button', { class: 'btn small grow', onclick: async () => { await api(`/api/people/${item.id}`, { method: 'PATCH', body: { unmatched: false, hidden: false } }); c.remove(); } }, 'Move back')
          : !item.name ? h('button', { class: 'btn small grow', onclick: async () => { await api(`/api/people/${item.id}`, { method: 'PATCH', body: { unmatched: true } }); done('Left unmatched.'); } }, 'Leave unmatched') : null));
    return c;
  }
  const dl = h('datalist', { id: 'people-names' });
  api('/api/people').then(ps => ps.forEach(pp => dl.append(h('option', { value: pp.name })))).catch(() => {});
  el.append(dl);
  await load(true, back ? Math.min(400, Math.max(40, back.count)) : 40);
  if (back) {
    const y = back.y;
    // try a few times while pictures load and the page grows to its full height
    let moved = false;
    const stop = () => { moved = true; };
    window.addEventListener('touchstart', stop, { once: true, passive: true });
    window.addEventListener('wheel', stop, { once: true, passive: true });
    [0, 150, 400, 800, 1500].forEach(ms => setTimeout(() => { if (!moved) window.scrollTo(0, y); }, ms));
  }
  // Leaving the page any other way (Back from a photo, a menu link…) also remembers the spot.
  return () => { if (!facesSpot) remember(); };
}

export async function faceGroup(el, p) {
  const pr = await api(`/api/people/${p.id}`);
  const sel = new Set();
  const grid = h('div', { class: 'facegrid' });
  const bar = h('div', { class: 'row', style: { marginBottom: '14px' } });
  el.append(head(pr.name || 'Unnamed group', `${num(pr.count)} faces. Tap faces that are NOT this person, then fix them.`, [
    h('button', { class: 'btn', onclick: async () => {
      const name = await promptDialog({ title: pr.name ? 'Rename' : 'Name this person', label: 'Name', value: pr.name || '' });
      if (!name) return;
      try {
        const r = await nameSafely(name, (extra) => api(`/api/people/${p.id}`, { method: 'PATCH', body: { name, ...extra } }));
        if (!r) return;
        toast(r.merged ? `Merged into ${name}.` : 'Saved.'); go(`#/admin/faces/${r.id}`);
      } catch (e) { errToast(e); }
    } }, icon('edit'), pr.name ? 'Rename' : 'Name'),
    pr.name ? h('a', { class: 'btn', href: `#/people/${p.id}` }, 'See photos') : null,
  ].filter(Boolean)), bar, grid);
  const drawBar = () => {
    bar.innerHTML = '';
    if (!sel.size) { bar.append(h('span', { class: 'hint' }, 'Select faces to move them to someone else, mark them as “not a face”, or pick one as their picture.')); return; }
    bar.append(h('b', {}, `${sel.size} selected`),
      sel.size === 1 ? h('button', { class: 'btn small', onclick: async () => {
        try { await api(`/api/people/${p.id}`, { method: 'PATCH', body: { cover_face_id: [...sel][0] } }); toast('Picture updated.'); sel.clear(); grid.querySelectorAll('.f.sel').forEach(x => x.classList.remove('sel')); drawBar(); }
        catch (e) { errToast(e); }
      } }, 'Use as their picture') : null,
      h('button', { class: 'btn small', onclick: async () => {
        const name = await promptDialog({ title: 'Move to…', label: 'Who are they?', placeholder: 'Type a name' });
        if (!name) return;
        const ids = [...sel];
        // Ask once which person is meant, then move every selected face there.
        const first = await nameSafely(name, (extra) => api(`/api/faces/${ids[0]}`, { method: 'PATCH', body: { name, ...extra } })).catch(errToast);
        if (!first) return;
        for (const f of ids.slice(1)) await api(`/api/faces/${f}`, { method: 'PATCH', body: { person_id: first.person_id } });
        toast(`Moved to ${name}.`); reload();
      } }, 'Move to someone else'),
      h('button', { class: 'btn small', onclick: async () => { for (const f of sel) await api(`/api/faces/${f}`, { method: 'PATCH', body: { unassign: true } }); toast('Removed from this person.'); reload(); } }, 'Remove from this person'),
      h('button', { class: 'btn small ghost', onclick: async () => { for (const f of sel) await api(`/api/faces/${f}`, { method: 'PATCH', body: { reject: true } }); toast('Faces removed. The photos are untouched.'); reload(); } }, 'Remove faces'),
      h('button', { class: 'btn small ghost', onclick: () => { sel.clear(); grid.querySelectorAll('.f.sel').forEach(x => x.classList.remove('sel')); drawBar(); } }, 'Clear'));
  };
  async function reload() {
    sel.clear(); grid.innerHTML = '';
    const fs = await api(`/api/people/${p.id}/faces?limit=500`);
    if (!fs.length) { grid.append(h('div', { class: 'empty', style: { gridColumn: '1/-1' } }, 'No faces left in this group.')); }
    fs.forEach(f => {
      const tile = h('div', { class: 'f', title: 'Select' }, h('img', { src: faceUrl(f.id), alt: '', loading: 'lazy' }),
        h('button', { class: 'x', 'aria-label': 'Open photo', onclick: (e) => { e.stopPropagation(); openViewer({ items: [{ id: f.asset_id, v: 0, k: 'i', w: 1, h: 1 }], index: 0 }); } }, icon('photos')));
      tile.addEventListener('click', () => { if (sel.has(f.id)) sel.delete(f.id); else sel.add(f.id); tile.classList.toggle('sel'); drawBar(); });
      grid.append(tile);
    });
    drawBar();
  }
  reload();
}

// ---------------------------------------------------------------- Duplicates

export async function duplicates(el, p, q) {
  let kind = q.kind || 'exact', autoPicked = false, offset = 0, counts = {};
  const groups = new Map(); // id -> { g, keep, sec, checkbox }
  const selected = new Set();
  const tabs = h('div', { class: 'tabs' });
  const allBar = h('div', { class: 'row', style: { marginTop: '14px' } });
  const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '18px', marginTop: '14px' } });
  const more = h('div', { style: { textAlign: 'center', padding: '16px' } });
  const selBar = h('div', { class: 'card hidden dupbar' });
  el.append(head('Library › Duplicates', 'Nothing happens to your files until you pick an action and confirm it.', [
    h('button', { class: 'btn', onclick: async () => { await post('/api/admin/jobs/duplicates'); toast('Checking for duplicates again…'); } }, 'Check again')]),
    tabs, allBar, list, more, selBar);

  const openCount = () => kind === 'kept' ? (counts['exact:kept'] || 0) + (counts['similar:kept'] || 0) : (counts[`${kind}:open`] || 0);

  async function load(reset = true) {
    if (reset) { offset = 0; groups.clear(); selected.clear(); list.innerHTML = ''; }
    let r;
    try { r = await api(`/api/admin/duplicates?kind=${kind === 'kept' ? 'exact' : kind}&status=${kind === 'kept' ? 'kept' : 'open'}&limit=30&offset=${offset}`); } catch (e) { return errToast(e); }
    counts = r.counts;
    if (!q.kind && !autoPicked && kind === 'exact' && !counts['exact:open'] && counts['similar:open']) { autoPicked = true; kind = 'similar'; return load(); }
    offset += r.groups.length;
    if (!r.groups.length && !groups.size) list.append(h('div', { class: 'empty' }, icon('dup'), kind === 'kept' ? 'No groups marked “keep both”.' : 'No duplicates found.'));
    r.groups.forEach(g => list.append(group(g)));
    drawTabs();
    more.innerHTML = '';
    if (r.groups.length === 30) more.append(h('button', { class: 'btn', onclick: () => load(false) }, 'Show more'));
    drawSel();
  }

  function drawTabs() {
    tabs.innerHTML = '';
    [['exact', `Exact copies · ${num(counts['exact:open'] || 0)}`], ['similar', `Look the same · ${num(counts['similar:open'] || 0)}`], ['kept', `Kept both · ${num((counts['exact:kept'] || 0) + (counts['similar:kept'] || 0))}`]]
      .forEach(([k, label]) => tabs.append(h('button', { class: kind === k ? 'on' : '', onclick: () => { kind = k; load(); } }, label)));
    allBar.innerHTML = '';
    if (kind === 'kept' || !openCount()) return;
    allBar.append(
      h('button', { class: 'btn small', onclick: () => { groups.forEach((x, id) => { selected.add(id); x.checkbox.checked = true; }); drawSel(); } }, `Select all shown (${groups.size})`),
      kind === 'exact' && state.allowDelete ? h('button', { class: 'btn small warn', onclick: () => doAll() }, `Delete extra copies in all ${num(openCount())} groups…`) : null,
      h('span', { class: 'hint' }, kind === 'similar' ? '“Look the same” are resized or re-saved copies. Check them by eye before deleting.' : 'Exact copies are byte-for-byte identical files.'));
  }

  function drawSel() {
    selBar.innerHTML = '';
    selBar.classList.toggle('hidden', !selected.size);
    if (!selected.size) return;
    const n = selected.size;
    selBar.append(h('b', { class: 'grow' }, `${n} group${n > 1 ? 's' : ''} selected`),
      h('button', { class: 'btn small primary', onclick: () => doSelected('hide') }, 'Hide extra copies'),
      state.allowDelete ? h('button', { class: 'btn small warn', onclick: () => doSelected('delete') }, 'Delete extra copies…') : null,
      h('button', { class: 'btn small ghost', onclick: () => doSelected('keep_both') }, 'Keep both'),
      h('button', { class: 'btn small ghost', onclick: () => { selected.clear(); groups.forEach(x => { x.checkbox.checked = false; }); drawSel(); } }, 'Clear'));
  }

  async function doSelected(action) {
    const picks = [...selected].map(id => { const e = groups.get(id); return e.keep ? { id, keep: e.keep } : (action === 'delete' ? { id, delete_all: true } : { id, keep: e.g.suggest_keep }); });
    const files = picks.reduce((s, x) => s + groups.get(x.id).g.members.length - (x.delete_all ? 0 : 1), 0);
    const allGone = picks.filter(x => x.delete_all).length;
    if (action === 'delete' && !await confirmDialog({ title: `Delete ${files} photo${files === 1 ? '' : 's'}?`, text: `From ${picks.length} group${picks.length > 1 ? 's' : ''}. The copy marked “Keep” in each group stays${allGone ? ` (except ${allGone} group${allGone > 1 ? 's' : ''} where you chose to delete every copy)` : ''}. They go to the Recycle Bin, where you can restore them until you empty it.`, confirmLabel: 'Send to Recycle Bin', danger: true, typeWord: 'DELETE' })) return;
    try {
      const r = await post('/api/admin/duplicates/bulk', { action, groups: picks, confirm: action === 'delete' ? 'DELETE' : undefined });
      if (r.errors && r.errors.length) toast(r.errors[0], true);
      toast(action === 'delete' ? `${num(r.moved)} copies moved to the Recycle Bin.` : action === 'hide' ? `Extra copies hidden in ${r.groups} groups.` : `Kept both in ${r.groups} groups.`);
      load();
    } catch (e) { errToast(e); }
  }

  async function doAll() {
    const n = openCount();
    if (!await confirmDialog({ title: `Delete the extra copies in all ${num(n)} groups?`, text: 'In every group of exact copies, the suggested copy is kept (the one with names, tags, albums or location, then the largest). The identical extras go to the Recycle Bin, where you can restore them until you empty it.', confirmLabel: 'Delete extra copies', danger: true, typeWord: 'DELETE' })) return;
    toast('Working… this can take a minute for lots of groups.');
    try {
      const r = await post('/api/admin/duplicates/bulk', { action: 'delete', all_kind: 'exact', confirm: 'DELETE' });
      if (r.errors && r.errors.length) toast(r.errors[0], true);
      toast(`${num(r.moved)} copies from ${num(r.groups)} groups moved to the Recycle Bin.`);
      load();
    } catch (e) { errToast(e); }
  }

  function group(g) {
    const entry = { g, keep: g.suggest_keep };
    const sec = h('section', { class: 'card' });
    const buttons = h('div', { class: 'row' });
    const copies = h('div', { class: 'dupgrid' });
    const draw = () => {
      copies.innerHTML = '';
      g.members.forEach(m => {
        const isKeep = m.id === entry.keep;
        const gone = !entry.keep;
        copies.append(h('div', { class: 'dupcopy' + (isKeep ? ' keep' : '') + (gone ? ' gone' : ''), onclick: () => { entry.keep = m.id; draw(); drawButtons(); pick(); }, role: 'radio', 'aria-checked': isKeep ? 'true' : 'false', tabindex: 0 },
          h('div', { class: 'img' }, img(thumbUrl(m.id, m.thumb_ver)), h('span', { class: 'tag' }, gone ? 'Will be deleted' : isKeep ? (m.id === g.suggest_keep ? 'Keep (suggested)' : 'Keep') : 'Extra copy')),
          h('div', { class: 'mono muted', style: { wordBreak: 'break-all' } }, `${m.library} / ${m.path}`),
          h('div', { style: { fontSize: '13px', color: 'var(--text2)' } }, `${m.width || '?'} × ${m.height || '?'} · ${fmtBytes(m.size)}`),
          h('div', { class: 'hint' }, [m.named_faces ? `${m.named_faces} named face${m.named_faces > 1 ? 's' : ''}` : null, m.tags ? `${m.tags} tags` : null, m.albums ? 'in an album' : null, m.lat ? 'has location' : null].filter(Boolean).join(' · ') || 'No extra info')));
      });
    };
    draw();
    // Choosing what to keep (or Delete all) also ticks the group, ready for the bar at the bottom.
    function pick() {
      if (kind === 'kept' || selected.has(g.id)) return;
      selected.add(g.id); cb.checked = true; drawSel();
    }
    const cb = h('input', { type: 'checkbox', 'aria-label': 'Select this group', style: { width: '22px', height: '22px', accentColor: 'var(--accent)' },
      onchange: () => { cb.checked ? selected.add(g.id) : selected.delete(g.id); drawSel(); } });
    entry.checkbox = cb;
    entry.sec = sec;
    groups.set(g.id, entry);
    const done = () => { sec.remove(); groups.delete(g.id); selected.delete(g.id); drawSel(); drawTabs(); };
    const others = () => g.members.length - 1;
    const both = g.members.length === 2 ? 'both' : `all ${g.members.length}`;
    function drawButtons() {
      buttons.innerHTML = '';
      if (kind === 'kept') { buttons.append(h('button', { class: 'btn', onclick: async () => { await post(`/api/admin/duplicates/${g.id}`, { action: 'reopen' }); done(); } }, 'Check this group again')); return; }
      if (!entry.keep) {
        // "Delete both" is chosen: every copy goes
        buttons.append(
          h('button', { class: 'btn small warn', onclick: async () => {
            if (!await confirmDialog({ title: `Delete ${both} copies?`, text: 'No copy of this photo will be kept. They go to the Recycle Bin, where you can restore them until you empty it.', confirmLabel: 'Send to Recycle Bin', danger: true })) return;
            try {
              const r = await post(`/api/admin/duplicates/${g.id}`, { action: 'delete', delete_all: true, confirm: 'DELETE' });
              if (r.errors && r.errors.length) toast(r.errors[0], true); else { toast('Moved to the Recycle Bin.'); done(); }
            } catch (e) { errToast(e); }
          } }, `Delete ${both} now`),
          h('button', { class: 'btn small ghost', onclick: () => { entry.keep = g.suggest_keep; draw(); drawButtons(); } }, 'Undo'),
          h('button', { class: 'btn small ghost', style: { marginLeft: 'auto' }, onclick: done }, 'Skip'));
        return;
      }
      buttons.append(
        h('button', { class: 'btn small', onclick: async () => {
          try { await post(`/api/admin/duplicates/${g.id}`, { action: 'hide', keep: entry.keep }); toast('Extra copy hidden. The file was not touched.'); done(); } catch (e) { errToast(e); }
        } }, `Hide the extra cop${others() > 1 ? 'ies' : 'y'}`),
        state.allowDelete ? h('button', { class: 'btn small warn', onclick: async () => {
          if (!await confirmDialog({ title: `Delete ${others()} photo${others() > 1 ? 's' : ''}?`, text: 'The extra copy goes to the Recycle Bin. You can restore it until you empty the bin.', confirmLabel: 'Send to Recycle Bin', danger: true })) return;
          try {
            const r = await post(`/api/admin/duplicates/${g.id}`, { action: 'delete', keep: entry.keep, confirm: 'DELETE' });
            if (r.errors && r.errors.length) toast(r.errors[0], true); else { toast('Moved to the Recycle Bin.'); done(); }
          } catch (e) { errToast(e); }
        } }, `Delete extra cop${others() > 1 ? 'ies' : 'y'}`) : null,
        h('button', { class: 'btn small ghost', onclick: async () => { await post(`/api/admin/duplicates/${g.id}`, { action: 'keep_both' }); toast(g.members.length === 2 ? 'Kept both.' : 'Kept all.'); done(); } }, `Keep ${both}`),
        state.allowDelete ? h('button', { class: 'btn small ghost danger-text', onclick: () => {
          entry.keep = 0; draw(); drawButtons(); pick();
        } }, `Delete ${both}`) : null,
        h('button', { class: 'btn small ghost', style: { marginLeft: 'auto' }, onclick: done }, 'Skip'));
    }
    sec.append(
      h('div', { class: 'row' },
        kind !== 'kept' ? h('label', { class: 'check', style: { minHeight: 0 } }, cb, 'Select') : null,
        h('span', { class: 'badge green' }, g.kind === 'exact' ? '100% identical' : 'Look the same'),
        h('span', { class: 'hint' }, fmtDateTime(g.members[0]?.taken_at).date)),
      copies,
      buttons,
      h('span', { class: 'hint' }, 'Tap a copy to choose which one to keep.'));
    drawButtons();
    return sec;
  }
  load();
}

// ---------------------------------------------------------------- Blurry photos
export async function quality(el, p, q) {
  let level = ['very', 'blurry', 'soft', 'dark'].includes(q.level) ? q.level : 'blurry';
  const tabs = h('div', { class: 'tabs' });
  const info = h('div', { class: 'hint', style: { margin: '10px 0 4px' } });
  const gridWrap = h('div');
  el.append(head('Library › Blurry photos', 'Photos that look blurry or too dark, so you can decide what to delete. Nothing is deleted unless you choose to.'),
    tabs, info, gridWrap);
  let grid = null, timer = null, dead = false;
  const labels = { very: 'Very blurry', blurry: 'Blurry', soft: 'Slightly soft', dark: 'Too dark' };
  const explain = {
    very: 'Only the worst ones. Almost all of these are photos nobody wants.',
    blurry: 'Clearly blurry or shaky. A good place to start.',
    soft: 'Includes photos that are just a little soft. Check these more carefully.',
    dark: 'Nearly black photos, like pocket shots or a covered lens.',
  };
  async function counts() {
    let c;
    try { c = await api(`/api/admin/quality?level=${level === 'dark' ? 'blurry' : level}`); } catch (e) { return; }
    if (dead) return;
    tabs.innerHTML = '';
    Object.entries(labels).forEach(([k, label]) => tabs.append(h('button', { class: level === k ? 'on' : '', onclick: () => { level = k; history.replaceState(null, '', `#/admin/quality?level=${k}`); show(); } }, `${label} · ${num(c[k] || 0)}`)));
    const left = c.total - c.checked;
    info.innerHTML = '';
    info.append(explain[level],
      left > 0 ? h('div', { style: { marginTop: '6px', color: 'var(--accent)' } }, `Still checking: ${num(c.checked)} of ${num(c.total)} photos done. The list grows as it goes.`) : null,
      !c.enabled ? h('div', { style: { marginTop: '6px', color: 'var(--orange)' } }, 'Blur checking is turned off in Settings.') : null,
      h('div', { style: { marginTop: '6px' } }, 'Select photos to delete them, or tap “Looks fine” so they stop showing up here.'));
    clearTimeout(timer);
    if (left > 0) timer = setTimeout(counts, 10000);
  }
  function show() {
    if (grid) grid.destroy();
    gridWrap.innerHTML = '';
    grid = new PhotoGrid({
      query: level === 'dark' ? { dark: 1 } : { blurry: level },
      emptyText: 'Nothing here. Nice!',
      extraActions: [{ icon: 'check', label: 'Looks fine', run: async (ids, g) => {
        try {
          await api('/api/assets/bulk', { method: 'POST', body: { ids, action: 'quality_ok' } });
          toast(`${num(ids.length)} marked as fine. They won’t show up here again.`);
          g.clearSelection(); g.reload(); counts();
        } catch (e) { errToast(e); }
      } }],
    });
    grid.mount(gridWrap);
    counts();
  }
  show();
  return () => { dead = true; clearTimeout(timer); if (grid) grid.destroy(); };
}

// Runs a "Look for…" check in the background worker and keeps the page up to
// date. The result is saved on the server, so the page can be closed.
function bgCheck({ what, url, status, findBtn, idleLabel, onResult }) {
  let timer = null, dead = false, lastKey = null;
  async function refresh(force = false) {
    clearTimeout(timer);
    let r;
    try { r = await api(url); } catch (e) { if (!dead) timer = setTimeout(refresh, 5000); return; }
    if (!dead) draw(r, force);
  }
  function draw(r, force = false) {
    const busy = r.state === 'running' || r.state === 'queued';
    status.innerHTML = '';
    findBtn.disabled = busy;
    findBtn.textContent = busy ? 'Looking…' : (r.state === 'never' ? idleLabel : 'Look again');
    if (busy) {
      status.append(h('div', { class: 'card', style: { gap: '8px', marginTop: '14px' } },
        h('div', { class: 'row' },
          h('b', { class: 'grow' }, r.state === 'queued' ? 'Starting…' : `Looking… ${num(r.checked || 0)} folders checked`),
          h('button', { class: 'btn small ghost', onclick: async () => { try { await post('/api/admin/cleanup/stop', { what }); toast('Stopping…'); } catch (e) { errToast(e); } } }, 'Stop')),
        r.current ? h('div', { class: 'mono muted', style: { wordBreak: 'break-all', fontSize: '13px' } }, r.current) : null,
        r.worker_ok === false ? h('div', { class: 'hint', style: { color: 'var(--orange)' } }, 'The background worker isn’t answering right now. It will start once the worker is back.') : null,
        h('div', { class: 'hint' }, 'You can leave this page. It keeps going in the background, and the results will be here when you come back.')));
      timer = setTimeout(refresh, 1500);
    } else if (r.state === 'done') {
      const took = Math.max(1, Math.round((r.finished_at || 0) - (r.started_at || 0)));
      status.append(h('div', { class: 'hint', style: { marginTop: '14px' } }, `Last checked ${fmtAgo(r.finished_at)} · looked through ${num(r.checked || 0)} folders in ${took < 90 ? `${took} s` : `${Math.round(took / 60)} min`}.`));
    } else if (r.state === 'stopped' || r.state === 'error') {
      status.append(h('div', { class: 'hint', style: { marginTop: '14px', color: 'var(--orange)' } },
        `${r.error || 'The last look didn’t finish.'}${r.libraries.length ? ' Showing the results from the last look that did finish.' : ''}`));
    }
    const key = `${r.state}:${r.finished_at}`;
    if (force || key !== lastKey) { lastKey = key; onResult(r, busy); }
  }
  return {
    refresh,
    async start() {
      try { const r = await post(url + '/look'); draw(r, true); } catch (e) { errToast(e); }
    },
    destroy() { dead = true; clearTimeout(timer); },
  };
}

// ---------------------------------------------------------------- Empty folders
export async function emptyFolders(el) {
  const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '18px', marginTop: '14px' } });
  const selBar = h('div', { class: 'card hidden dupbar' });
  const selected = new Map(); // key -> {library_id, rel, cb}
  const findBtn = h('button', { class: 'btn primary', onclick: () => checker.start() }, 'Look for empty folders');
  const status = h('div');
  el.append(head('Library › Empty folders', 'Finds folders with nothing in them so you can tidy up. Nothing is removed until you pick folders and confirm.', [findBtn]),
    status, body, selBar);
  const checker = bgCheck({ what: 'empty_folders', url: '/api/admin/empty-folders', status, findBtn, idleLabel: 'Look for empty folders', onResult: render });

  function drawSel() {
    selBar.innerHTML = '';
    selBar.classList.toggle('hidden', !selected.size);
    if (!selected.size) return;
    const n = selected.size;
    selBar.append(h('b', { class: 'grow' }, `${num(n)} folder${n > 1 ? 's' : ''} selected`),
      h('button', { class: 'btn small warn', onclick: removeSelected }, `Remove ${n > 1 ? 'them' : 'it'}…`),
      h('button', { class: 'btn small ghost', onclick: () => { selected.forEach(x => { x.cb.checked = false; }); selected.clear(); drawSel(); } }, 'Clear'));
  }

  async function removeSelected() {
    const n = selected.size;
    if (!await confirmDialog({ title: `Remove ${num(n)} empty folder${n > 1 ? 's' : ''}?`,
      text: 'Each folder is checked again right before it is removed. If anything was put in it since you looked, it is left alone. Empty folders do not go to the Recycle Bin, since there is nothing in them to restore.',
      confirmLabel: 'Remove folders', danger: true })) return;
    try {
      const r = await post('/api/admin/empty-folders/remove', { items: [...selected.values()].map(x => ({ library_id: x.library_id, rel: x.rel })) });
      if (r.errors.length) toast(r.errors[0], true);
      toast(`Removed ${num(r.removed)} empty folder${r.removed === 1 ? '' : 's'}.`);
      selected.clear(); drawSel(); checker.refresh(true);
    } catch (e) { errToast(e); }
  }

  function render(r, busy) {
    selected.clear(); drawSel();
    body.innerHTML = '';
    if (busy) { body.append(h('div', { class: 'empty' }, icon('folder'), 'Looking through every folder… the list shows up here when it’s done.')); return; }
    if (r.state === 'never' || (!r.libraries.length && r.state !== 'done')) { body.append(h('div', { class: 'empty' }, icon('folder'), 'Press “Look for empty folders” to check all your libraries.')); return; }
    const total = r.libraries.reduce((s, l) => s + (l.folders || []).length, 0);
    body.append(h('div', { class: 'hint' }, total ? `${num(total)} empty folder${total > 1 ? 's' : ''} found.` : 'No empty folders found.'));
    r.libraries.forEach(lib => {
      if (!(lib.folders || []).length) return;
      const rows = h('div', { style: { display: 'flex', flexDirection: 'column' } });
      const cbs = [];
      let shown = 0;
      const moreBtn = h('button', { class: 'btn small', style: { marginTop: '10px', alignSelf: 'center' }, onclick: () => drawMore() });
      const drawMore = () => { lib.folders.slice(shown, shown + 300).forEach(f => {
        const key = `${lib.id}:${f.rel}`;
        const cb = h('input', { type: 'checkbox', disabled: !lib.writable, checked: selected.has(key), style: { width: '20px', height: '20px', accentColor: 'var(--accent)', flex: 'none' },
          onchange: () => { cb.checked ? selected.set(key, { library_id: lib.id, rel: f.rel, cb }) : selected.delete(key); drawSel(); } });
        if (selected.has(key)) selected.get(key).cb = cb;
        cbs.push([key, f, cb]);
        const notes = [f.subfolders ? `has ${num(f.subfolders)} empty folder${f.subfolders > 1 ? 's' : ''} inside` : null,
          f.junk_count ? `only system files: ${f.junk.join(', ')}${f.junk_count > f.junk.length ? '…' : ''}` : null].filter(Boolean).join(' · ')
          || 'Completely empty: no files and no folders inside';
        rows.append(h('label', { class: 'check', style: { alignItems: 'flex-start', padding: '10px 0', borderTop: '1px solid var(--line)', minHeight: 0 } }, cb,
          h('div', { style: { minWidth: 0 } }, h('div', { class: 'mono', style: { wordBreak: 'break-all' } }, f.rel), notes ? h('div', { class: 'hint' }, notes) : null)));
      });
        shown = Math.min(lib.folders.length, shown + 300);
        moreBtn.classList.toggle('hidden', shown >= lib.folders.length);
        moreBtn.textContent = `Show more (${num(lib.folders.length - shown)} left)`;
      };
      drawMore();
      body.append(h('section', { class: 'card' },
        h('div', { class: 'row' }, h('div', { class: 'grow' }, h('b', {}, `${num(lib.folders.length)} empty folder${lib.folders.length > 1 ? 's' : ''}`), h('div', { class: 'hint' }, `In the “${lib.name}” library`)),
          lib.writable ? h('button', { class: 'btn small', onclick: () => { lib.folders.forEach(f => { const key = `${lib.id}:${f.rel}`; if (!selected.has(key)) selected.set(key, { library_id: lib.id, rel: f.rel, cb: { checked: true } }); }); cbs.forEach(([key, f, cb]) => { cb.checked = true; selected.set(key, { library_id: lib.id, rel: f.rel, cb }); }); drawSel(); } }, 'Select all') : null),
        lib.capped ? h('div', { class: 'hint' }, 'Showing the first 20,000. Remove these and look again to see the rest.') : null,
        !lib.writable ? h('div', { class: 'hint', style: { color: 'var(--orange, #F5A35C)' } }, 'This library is read-only, so folders can’t be removed. Change its folder mapping in Unraid to Read/Write if you want to.') : null,
        rows, moreBtn));
    });
  }
  checker.refresh(true);
  return () => checker.destroy();
}

// ---------------------------------------------------------------- Non-photo files
export async function otherFiles(el, p, q) {
  let tab = q.tab === 'removed' ? 'removed' : 'found';
  let found = null; // last scan result
  const selected = new Map(); // key -> {library_id, rel}
  const tabs = h('div', { class: 'tabs' });
  const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '18px', marginTop: '14px' } });
  const selBar = h('div', { class: 'card hidden dupbar' });
  const findBtn = h('button', { class: 'btn primary', onclick: () => checker.start() }, 'Look for non-photo files');
  const status = h('div');
  el.append(head('Library › Non-photo files', 'Finds files that aren’t photos or videos, like .DS_Store, temp files and text files. Photo and video files of any kind are never listed.', [findBtn]),
    tabs, status, body, selBar);
  let busyNow = false;
  const checker = bgCheck({ what: 'other_files', url: '/api/admin/other-files', status, findBtn, idleLabel: 'Look for non-photo files',
    onResult: (r, busy) => { found = r; busyNow = busy; selected.clear(); drawSel(); drawTabs(r.removed); if (tab === 'found') drawFound(); } });

  function drawTabs(removedCount) {
    tabs.innerHTML = '';
    [['found', 'Found'], ['removed', `Removed${removedCount != null ? ` · ${num(removedCount)}` : ''}`]].forEach(([k, label]) =>
      tabs.append(h('button', { class: tab === k ? 'on' : '', onclick: () => { tab = k; selected.clear(); drawSel(); k === 'found' ? drawFound() : drawRemoved(); drawTabs(removedCount); } }, label)));
    findBtn.classList.toggle('hidden', tab !== 'found');
    status.classList.toggle('hidden', tab !== 'found');
  }

  function drawSel() {
    selBar.innerHTML = '';
    selBar.classList.toggle('hidden', !selected.size);
    if (!selected.size) return;
    const n = selected.size;
    const clear = h('button', { class: 'btn small ghost', onclick: () => { selected.clear(); body.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; }); drawSel(); } }, 'Clear');
    if (tab === 'found') {
      const bytes = [...selected.values()].reduce((s, x) => s + (x.size || 0), 0);
      selBar.append(h('b', { class: 'grow' }, `${num(n)} file${n > 1 ? 's' : ''} selected · ${fmtBytes(bytes)}`),
        h('button', { class: 'btn small warn', onclick: removeSelected }, 'Move to Recycle Bin…'), clear);
    } else {
      selBar.append(h('b', { class: 'grow' }, `${num(n)} file${n > 1 ? 's' : ''} selected`),
        h('button', { class: 'btn small primary', onclick: async () => {
          try { const r = await post('/api/admin/other-files/restore', { ids: [...selected.keys()] }); if (r.errors.length) toast(r.errors[0], true); toast(`${num(r.restored)} put back where they were.`); selected.clear(); drawSel(); drawRemoved(); } catch (e) { errToast(e); }
        } }, 'Restore'),
        h('button', { class: 'btn small warn', onclick: async () => {
          if (!await confirmDialog({ title: `Delete ${num(n)} file${n > 1 ? 's' : ''} forever?`, text: 'This can’t be undone.', confirmLabel: 'Delete forever', danger: true })) return;
          try { const r = await post('/api/admin/other-files/purge', { ids: [...selected.keys()] }); toast(`${num(r.purged)} deleted forever.`); selected.clear(); drawSel(); drawRemoved(); } catch (e) { errToast(e); }
        } }, 'Delete forever'), clear);
    }
  }

  async function removeSelected() {
    const n = selected.size;
    if (!await confirmDialog({ title: `Move ${num(n)} file${n > 1 ? 's' : ''} to the Recycle Bin?`,
      text: 'Each file is checked again first, and anything that looks like a photo or video is left alone. You can put them back from the Removed tab until you empty the Recycle Bin.',
      confirmLabel: 'Move to Recycle Bin', danger: true })) return;
    try {
      const r = await post('/api/admin/other-files/remove', { items: [...selected.values()].map(x => ({ library_id: x.library_id, rel: x.rel })) });
      if (r.errors.length) toast(r.errors[0], true);
      toast(`Moved ${num(r.moved)} file${r.moved === 1 ? '' : 's'} to the Recycle Bin.`);
      checker.refresh(true);
    } catch (e) { errToast(e); }
  }

  function drawFound() {
    body.innerHTML = '';
    if (!found) return;
    if (busyNow) { body.append(h('div', { class: 'empty' }, icon('doc'), 'Looking through every folder… the list shows up here when it’s done.')); return; }
    if (found.state === 'never' || (!found.libraries.length && found.state !== 'done')) { body.append(h('div', { class: 'empty' }, icon('doc'), 'Press “Look for non-photo files” to check all your libraries.')); return; }
    const all = [];
    found.libraries.forEach(lib => (lib.files || []).forEach(f => all.push({ ...f, lib })));
    const total = all.length, bytes = all.reduce((s, f) => s + f.size, 0);
    body.append(h('div', { class: 'hint' }, total ? `${num(total)} non-photo file${total > 1 ? 's' : ''} found (${fmtBytes(bytes)}).` : 'Nothing found. Your libraries only have photos and videos.'));
    found.libraries.filter(l => !l.writable && (l.files || []).length).forEach(l => body.append(h('div', { class: 'hint', style: { color: 'var(--orange)' } }, `${l.name} is read-only, so its files can be seen but not removed. Change its folder mapping in Unraid to Read/Write if you want to.`)));
    found.libraries.filter(l => l.capped).forEach(l => body.append(h('div', { class: 'hint' }, `${l.name}: showing the first 20,000. Remove some and look again to see the rest.`)));
    Object.keys(found.kinds).forEach(kind => {
      const files = all.filter(f => f.kind === kind);
      if (!files.length) return;
      const keyOf = f => `${f.lib.id}:${f.rel}`;
      const rows = h('div', { style: { display: 'flex', flexDirection: 'column' } });
      let shown = 0;
      const boxes = [];
      const drawMore = () => {
        files.slice(shown, shown + 300).forEach(f => {
          const key = keyOf(f);
          const cb = h('input', { type: 'checkbox', disabled: !f.lib.writable, checked: selected.has(key), style: { width: '20px', height: '20px', accentColor: 'var(--accent)', flex: 'none' },
            onchange: () => { cb.checked ? selected.set(key, { library_id: f.lib.id, rel: f.rel, size: f.size }) : selected.delete(key); drawSel(); } });
          boxes.push(cb);
          rows.append(h('label', { class: 'check', style: { alignItems: 'flex-start', padding: '9px 0', borderTop: '1px solid var(--line)', minHeight: 0 } }, cb,
            h('div', { style: { minWidth: 0 } },
              h('div', { class: 'mono', style: { wordBreak: 'break-all' } }, f.rel),
              h('div', { class: 'hint' }, `${f.lib.name} · ${fmtBytes(f.size)} · ${fmtAgo(f.mtime)}`))));
        });
        shown = Math.min(files.length, shown + 300);
        moreBtn.classList.toggle('hidden', shown >= files.length);
        moreBtn.textContent = `Show more (${num(files.length - shown)} left)`;
      };
      const moreBtn = h('button', { class: 'btn small', style: { marginTop: '10px', alignSelf: 'center' }, onclick: drawMore });
      const listWrap = h('div', { class: 'hidden', style: { display: 'flex', flexDirection: 'column' } }, rows, moreBtn);
      const toggle = h('button', { class: 'btn small ghost' }, 'Show files');
      toggle.onclick = () => { const open = listWrap.classList.toggle('hidden'); toggle.textContent = open ? 'Show files' : 'Hide files'; if (!open && !shown) drawMore(); };
      const kBytes = files.reduce((s, f) => s + f.size, 0);
      body.append(h('section', { class: 'card' },
        h('div', { class: 'row' },
          h('div', { class: 'grow' }, h('b', {}, found.kinds[kind]), h('div', { class: 'hint' }, `${num(files.length)} file${files.length > 1 ? 's' : ''} · ${fmtBytes(kBytes)}`)),
          toggle,
          h('button', { class: 'btn small', onclick: () => { files.forEach(f => { if (f.lib.writable) selected.set(keyOf(f), { library_id: f.lib.id, rel: f.rel, size: f.size }); }); boxes.forEach(c => { if (!c.disabled) c.checked = true; }); drawSel(); } }, 'Select all')),
        kind === 'apple' ? h('div', { class: 'hint' }, 'These hold the edits you made on your iPhone (crops, filters). EasyPhotos doesn’t use them, but other apps might.') : null,
        kind === 'docs' || kind === 'other' ? h('div', { class: 'hint' }, 'Have a quick look at these before removing them, in case something is worth keeping.') : null,
        listWrap));
    });
  }

  async function drawRemoved() {
    body.innerHTML = '';
    let r;
    try { r = await api('/api/admin/other-files/removed'); } catch (e) { return errToast(e); }
    drawTabs(r.files.length);
    if (!r.files.length) { body.append(h('div', { class: 'empty' }, icon('trash'), 'No removed non-photo files.')); return; }
    const boxes = [];
    body.append(h('div', { class: 'row' },
      h('span', { class: 'hint grow' }, 'These are in the Recycle Bin. Emptying the Recycle Bin deletes them for good.'),
      h('button', { class: 'btn small', onclick: () => { r.files.forEach(f => selected.set(f.id, f)); boxes.forEach(c => { c.checked = true; }); drawSel(); } }, 'Select all')));
    const rows = h('section', { class: 'card', style: { gap: 0 } });
    r.files.forEach(f => {
      const cb = h('input', { type: 'checkbox', style: { width: '20px', height: '20px', accentColor: 'var(--accent)', flex: 'none' },
        onchange: () => { cb.checked ? selected.set(f.id, f) : selected.delete(f.id); drawSel(); } });
      boxes.push(cb);
      rows.append(h('label', { class: 'check', style: { alignItems: 'flex-start', padding: '9px 0', borderTop: '1px solid var(--line)', minHeight: 0 } }, cb,
        h('div', { style: { minWidth: 0 } }, h('div', { class: 'mono', style: { wordBreak: 'break-all' } }, f.path),
          h('div', { class: 'hint' }, `${f.library} · ${fmtBytes(f.size)} · removed ${fmtAgo(f.deleted_at)}`))));
    });
    body.append(rows);
  }

  drawTabs(null);
  checker.refresh(true);
  if (tab === 'removed') drawRemoved();
  return () => checker.destroy();
}

// ---------------------------------------------------------------- Hidden & Recycle Bin

export async function hidden(el) {
  el.append(head('Hidden photos', 'Hidden photos don’t show anywhere for anyone. Select them and choose “Unhide” to bring them back.'));
  const g = new PhotoGrid({ query: { hidden: 1 }, hiddenView: true, emptyText: 'No hidden photos.' });
  g.mount(el);
  return () => g.destroy();
}

export async function recycle(el) {
  const sel = new Set();
  const body = h('div');
  const restoreBtn = h('button', { class: 'btn', disabled: true, onclick: async () => {
    try { const r = await post('/api/admin/recycle/restore', { ids: [...sel] }); toast(`${r.restored} restored to their folders.`); if (r.errors.length) toast(r.errors[0], true); load(); } catch (e) { errToast(e); }
  } }, icon('restore'), 'Restore selected');
  const headEl = head('Recycle Bin', 'Loading…', [restoreBtn, h('button', { class: 'btn danger', onclick: async () => {
    if (!await confirmDialog({ title: 'Permanently delete everything in the Recycle Bin?', text: 'This can’t be undone. The files will be gone from your server for good, along with their face names and tags.', confirmLabel: 'Delete forever', danger: true, typeWord: 'EMPTY' })) return;
    try { const r = await post('/api/admin/recycle/empty', { confirm: 'EMPTY' }); toast(`${r.purged + (r.other_purged || 0)} deleted forever.`); load(); } catch (e) { errToast(e); }
  } }, 'Empty Recycle Bin…')]);
  el.append(headEl, body);
  async function load() {
    sel.clear(); restoreBtn.disabled = true;
    const r = await api('/api/admin/recycle?limit=500');
    headEl.querySelector('.sub').textContent = `${num(r.count)} photos · ${fmtBytes(r.bytes)}. Restore puts each one back in its original folder.`;
    body.innerHTML = '';
    const others = await api('/api/admin/other-files/removed').catch(() => ({ files: [] }));
    if (others.files.length) body.append(h('a', { class: 'card', href: '#/admin/other-files?tab=removed', style: { flexDirection: 'row', alignItems: 'center', marginBottom: '14px', color: 'var(--text)' } },
      icon('doc'), h('span', { class: 'grow' }, `Also in the bin: ${num(others.files.length)} non-photo file${others.files.length > 1 ? 's' : ''} (${fmtBytes(others.files.reduce((s, f) => s + f.size, 0))})`), icon('next')));
    if (!r.items.length) { body.append(h('div', { class: 'empty' }, icon('trash'), others.files.length ? 'No photos in the Recycle Bin.' : 'The Recycle Bin is empty.')); return; }
    const updateBtn = () => { restoreBtn.disabled = !sel.size; restoreBtn.lastChild.textContent = sel.size ? `Restore selected (${sel.size})` : 'Restore selected'; };
    body.append(h('div', { class: 'card scrollx', style: { padding: '4px 0' } }, h('table', { class: 'table' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, ''), h('th', {}, 'File'), h('th', {}, 'Was in'), h('th', {}, 'Deleted'), h('th', {}, ''))),
      h('tbody', {}, r.items.map(it => {
        const cb = h('input', { type: 'checkbox', 'aria-label': `Select ${it.filename}`, style: { width: '20px', height: '20px', accentColor: 'var(--accent)' }, onchange: () => { cb.checked ? sel.add(it.id) : sel.delete(it.id); updateBtn(); } });
        return h('tr', {},
          h('td', {}, cb),
          h('td', {}, img(thumbUrl(it.id, it.thumb_ver), { style: { width: '64px', height: '48px', objectFit: 'cover', borderRadius: '6px', opacity: 1 } })),
          h('td', {}, it.filename),
          h('td', { class: 'mono muted' }, `${it.library} / ${it.folder || ''}`),
          h('td', { class: 'muted' }, fmtAgo(it.deleted_at)),
          h('td', { style: { whiteSpace: 'nowrap', textAlign: 'right' } },
            h('button', { class: 'btn small', onclick: async () => { await post('/api/admin/recycle/restore', { ids: [it.id] }); toast('Restored.'); load(); } }, 'Restore'),
            h('button', { class: 'btn small ghost', style: { color: 'var(--red)', border: 0 }, onclick: async () => {
              if (!await confirmDialog({ title: `Delete ${it.filename} forever?`, text: 'This can’t be undone.', confirmLabel: 'Delete forever', danger: true })) return;
              await post('/api/admin/recycle/purge', { ids: [it.id], confirm: 'DELETE' }); toast('Deleted forever.'); load();
            } }, 'Delete forever')));
      })))));
  }
  load();
}

// ---------------------------------------------------------------- Logs

export async function logs(el, p, q) {
  let which = q.which || 'worker', problems = q.problems === '1', paused = false;
  const pre = h('pre', { class: 'mono', style: { margin: 0, padding: '14px', background: 'var(--bg2)', border: '1px solid var(--line)', borderRadius: '12px', overflow: 'auto', maxHeight: 'calc(100vh - 260px)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.55 } });
  const fileHint = h('span', { class: 'hint' });
  const tabs = h('div', { class: 'tabs' });
  const probBox = h('input', { type: 'checkbox', checked: problems, onchange: () => { problems = probBox.checked; load(); } });
  const pauseBox = h('input', { type: 'checkbox', onchange: () => { paused = pauseBox.checked; } });
  el.append(head('Logs', 'What EasyPhotos has been doing. Newest at the bottom. Refreshes every few seconds.'),
    tabs, h('div', { class: 'row', style: { margin: '12px 0' } },
      h('label', { class: 'check' }, probBox, 'Only show problems'),
      h('label', { class: 'check' }, pauseBox, 'Pause auto-refresh'),
      h('span', { class: 'grow' }), fileHint), pre);
  const drawTabs = () => {
    tabs.innerHTML = '';
    [['worker', 'Background work (scanning, thumbnails, faces)'], ['web', 'Web server']].forEach(([k, l]) =>
      tabs.append(h('button', { class: which === k ? 'on' : '', onclick: () => { which = k; drawTabs(); load(); } }, l)));
  };
  async function load() {
    try {
      const r = await api(`/api/admin/logs?which=${which}&lines=500&problems=${problems ? 1 : 0}`);
      fileHint.textContent = r.file;
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
      pre.innerHTML = '';
      if (!r.lines.length) pre.append(problems ? 'No problems logged. 👍'.replace(' 👍', '') : 'Nothing logged yet.');
      r.lines.forEach(line => {
        const bad = / ERROR |Traceback/.test(line), warn = / WARNING /.test(line);
        pre.append(h('div', { style: { color: bad ? 'var(--red)' : warn ? 'var(--orange)' : 'var(--text2)' } }, line));
      });
      if (atBottom || !pre.dataset.loaded) pre.scrollTop = pre.scrollHeight;
      pre.dataset.loaded = '1';
    } catch (e) { errToast(e); }
  }
  drawTabs();
  await load();
  const t = setInterval(() => { if (!paused) load(); }, 4000);
  return () => clearInterval(t);
}

// ---------------------------------------------------------------- Users

const ROLE_INFO = {
  viewer: ['Viewer', 'Can only look at photos. No downloads.'],
  user: ['User', 'Can look at photos and download them.'],
  admin: ['Admin', 'Can do everything.'],
};

export async function users(el) {
  const box = h('div');
  el.append(head('Users', 'There is no sign-up. Add people here. They choose their own password the first time they sign in.',
    [h('button', { class: 'btn primary', onclick: () => editUser(null, load) }, '+ Add user')]), box);
  async function load() {
    const [list, libs] = await Promise.all([api('/api/admin/users'), api('/api/admin/libraries')]);
    const libName = Object.fromEntries(libs.map(l => [l.id, l.name]));
    box.innerHTML = '';
    box.append(h('div', { class: 'card scrollx', style: { padding: '4px 0' } }, h('table', { class: 'table', style: { minWidth: '720px' } },
      h('thead', {}, h('tr', {}, ['User', 'Role', 'Can see', 'Password', 'Last sign-in', ''].map(t => h('th', {}, t)))),
      h('tbody', {}, list.map(u => h('tr', {},
        h('td', {}, h('div', { class: 'row', style: { gap: '10px', flexWrap: 'nowrap' } }, h('span', { class: 'avatar', style: { width: '32px', height: '32px', fontSize: '12px' } }, u.username.slice(0, 2)), h('b', {}, u.username))),
        h('td', {}, h('span', { class: 'badge', style: { background: { admin: '#3A2E1A', user: '#1C3328', viewer: '#262A33' }[u.role], color: { admin: 'var(--orange)', user: 'var(--green)', viewer: 'var(--text2)' }[u.role], padding: '4px 10px' } }, ROLE_INFO[u.role][0])),
        h('td', { class: 'muted' }, u.role === 'admin' || u.all_libraries ? 'Everything' : (u.library_ids.map(i => libName[i]).filter(Boolean).join(', ') || 'Nothing')),
        h('td', {}, u.has_password ? h('span', { class: 'muted' }, 'Set') : h('span', { style: { color: 'var(--orange)' } }, 'Waiting for first sign-in')),
        h('td', { class: 'muted' }, fmtAgo(u.last_login)),
        h('td', { style: { textAlign: 'right' } }, h('button', { class: 'btn small', onclick: () => editUser(u, load, libs) }, 'Edit'))))))));
  }
  load();
}

async function editUser(u, after, libs = null) {
  libs = libs || await api('/api/admin/libraries');
  const name = h('input', { class: 'input', value: u ? u.username : '', disabled: !!u, autocapitalize: 'off', placeholder: 'e.g. grandma' });
  const roleBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } });
  let role = u ? u.role : 'viewer';
  const drawRoles = () => {
    roleBox.innerHTML = '';
    Object.entries(ROLE_INFO).forEach(([k, [label, desc]]) => roleBox.append(h('label', { class: 'opt', style: role === k ? { borderColor: 'var(--accent)', background: '#161C2B' } : null },
      h('input', { type: 'radio', name: 'role', checked: role === k, onchange: () => { role = k; drawRoles(); libBox.classList.toggle('hidden', role === 'admin'); } }),
      h('div', {}, h('b', {}, label), h('div', { class: 'hint' }, desc)))));
  };
  drawRoles();
  const all = h('input', { type: 'checkbox', checked: u ? u.all_libraries : true });
  const libChecks = libs.map(l => { const c = h('input', { type: 'checkbox', checked: u ? u.library_ids.includes(l.id) : false }); c._id = l.id; return h('label', { class: 'check' }, c, l.name); });
  const libList = h('div', { class: libs.length && !(u ? u.all_libraries : true) ? '' : 'hidden', style: { paddingLeft: '20px' } }, libChecks);
  all.addEventListener('change', () => libList.classList.toggle('hidden', all.checked));
  const libBox = h('div', { class: role === 'admin' ? 'hidden' : '' }, h('label', { class: 'check' }, all, 'Can see every library'), libList);
  const actions = [];
  if (u) {
    actions.push({ label: 'Delete user', cls: 'danger', run: async () => {
      if (!await confirmDialog({ title: `Delete ${u.username}?`, text: 'They won’t be able to sign in anymore. Photos are not affected.', confirmLabel: 'Delete user', danger: true })) return true;
      await api(`/api/admin/users/${u.id}`, { method: 'DELETE' }); toast('User deleted.'); after();
    } });
    actions.push({ label: 'Reset password', cls: 'ghost', run: async () => {
      if (!await confirmDialog({ title: `Reset ${u.username}’s password?`, text: 'They’ll be signed out. Next time they sign in with their username, they’ll choose a new password.', confirmLabel: 'Reset password' })) return true;
      await post(`/api/admin/users/${u.id}/reset-password`); toast('Password reset.'); after();
    } });
  }
  actions.push({ label: 'Cancel', cls: 'ghost' });
  actions.push({ label: u ? 'Save' : 'Add user', cls: 'primary', run: async () => {
    const body = { role, all_libraries: all.checked, library_ids: libChecks.map(l => l.querySelector('input')).filter(c => c.checked).map(c => c._id) };
    if (u) await api(`/api/admin/users/${u.id}`, { method: 'PATCH', body });
    else { body.username = name.value.trim(); await post('/api/admin/users', body); toast(`${body.username} added. Tell them to sign in with that username and choose a password.`); }
    after();
  } });
  modal({
    title: u ? `Edit ${u.username}` : 'Add a user',
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '14px' } },
      h('label', { class: 'field' }, 'Username', u ? null : h('span', { class: 'hint' }, 'They’ll pick their own password the first time they sign in.'), name),
      h('div', { class: 'field' }, 'Role', roleBox),
      libBox),
    actions,
  });
}

// ---------------------------------------------------------------- API keys

export async function apikeys(el) {
  const box = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '16px' } });
  const base = location.origin;
  el.append(head('API keys', 'Let other apps (dashboards, photo frames, Home Assistant) pull random photos. Each key only sees the folders you choose.',
    [h('button', { class: 'btn primary', onclick: () => keyDialog(null, load) }, '+ New API key')]), box);
  async function load() {
    const keys = await api('/api/admin/apikeys');
    box.innerHTML = '';
    if (!keys.length) box.append(h('div', { class: 'empty' }, icon('key'), 'No API keys yet.'));
    keys.forEach(k => box.append(h('section', { class: 'card' },
      h('div', { class: 'row' }, icon('key'), h('b', { class: 'grow' }, k.name), h('span', { class: 'mono muted' }, `${k.prefix}…`), h('span', { class: 'hint' }, `Last used: ${fmtAgo(k.last_used)}`),
        h('button', { class: 'btn small', onclick: () => keyDialog(k, load) }, 'Edit'),
        h('button', { class: 'btn small danger', onclick: async () => {
          if (!await confirmDialog({ title: `Delete “${k.name}”?`, text: 'Apps using this key will stop working.', confirmLabel: 'Delete key', danger: true })) return;
          await api(`/api/admin/apikeys/${k.id}`, { method: 'DELETE' }); load();
        } }, 'Delete')),
      h('div', { class: 'row', style: { gap: '6px' } }, h('span', { class: 'hint' }, 'Folders:'), (k.folder_labels.length ? k.folder_labels : ['All folders']).map(f => h('span', { class: 'chip' }, f)), k.include_videos ? h('span', { class: 'chip' }, 'Includes videos') : null))));
    box.append(h('section', { class: 'card' }, h('h2', {}, 'How to use it'),
      h('p', { class: 'hint', style: { margin: 0 } }, 'Send the key as the X-API-Key header, or add ?key=YOUR_KEY to the address.'),
      h('div', { class: 'mono', style: { display: 'flex', flexDirection: 'column', gap: '8px', color: 'var(--text2)' } },
        h('div', {}, h('b', {}, 'One random picture (for an <img> tag):'), h('br'), `${base}/api/v1/random/image?key=YOUR_KEY`),
        h('div', {}, h('b', {}, 'Several random photos as JSON:'), h('br'), `${base}/api/v1/random?count=10&key=YOUR_KEY`),
        h('div', {}, h('b', {}, 'Optional filters:'), h('br'), 'size=thumbnail|preview|original · person=Sarah · tag=Dog · album=Christmas · orientation=landscape|portrait'))));
  }
  load();
}

async function keyDialog(k, after) {
  const libs = await api('/api/admin/libraries');
  const name = h('input', { class: 'input', value: k ? k.name : '', placeholder: 'e.g. Homepage dashboard' });
  const videos = h('input', { type: 'checkbox', checked: k ? k.include_videos : false });
  const chosen = new Map((k ? k.folders : []).map(f => [`${f.library_id}|${f.folder}`, f]));
  const tree = h('div', { class: 'ftree' }, h('div', { class: 'spinner' }));
  const summary = h('div', { class: 'hint' });
  const drawSummary = () => { summary.textContent = chosen.size ? `${chosen.size} folder${chosen.size > 1 ? 's' : ''} chosen (sub-folders included)` : 'No folders chosen = every folder.'; };
  drawSummary();
  (async () => {
    tree.innerHTML = '';
    for (const lib of libs) {
      const folders = await api(`/api/admin/libraries/${lib.id}/folders`);
      for (const f of folders) {
        const key = `${lib.id}|${f.path}`;
        const depth = f.path ? f.path.split('/').length : 0;
        const cb = h('input', { type: 'checkbox', checked: chosen.has(key), onchange: () => { cb.checked ? chosen.set(key, { library_id: lib.id, folder: f.path }) : chosen.delete(key); drawSummary(); } });
        tree.append(h('label', { style: { paddingLeft: `${8 + depth * 20}px` } }, cb, icon('folder'), f.path ? f.path.split('/').pop() : h('b', {}, lib.name), h('span', { class: 'hint', style: { marginLeft: 'auto' } }, num(f.count))));
      }
    }
    if (!libs.length) tree.append(h('div', { class: 'hint' }, 'Add a library first.'));
  })().catch(errToast);
  modal({
    title: k ? 'Edit API key' : 'New API key', wide: true,
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
      h('label', { class: 'field' }, 'Name', name),
      h('div', { class: 'field' }, 'Which folders can this key pull from?', summary, tree),
      h('label', { class: 'check' }, videos, 'Include videos')),
    actions: [{ label: 'Cancel', cls: 'ghost' }, { label: k ? 'Save' : 'Create key', cls: 'primary', run: async () => {
      const body = { name: name.value, folders: [...chosen.values()], include_videos: videos.checked };
      if (k) { await api(`/api/admin/apikeys/${k.id}`, { method: 'PATCH', body }); after(); return; }
      const r = await post('/api/admin/apikeys', body);
      after();
      const copyBtn = h('button', { class: 'btn', onclick: () => { navigator.clipboard?.writeText(r.key).then(() => toast('Copied.')); } }, icon('copy'), 'Copy');
      modal({ title: 'Your new API key', body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
        h('p', { style: { margin: 0 } }, 'Copy it now. For safety it won’t be shown again.'), h('div', { class: 'keybox' }, r.key), copyBtn),
        actions: [{ label: 'Done', cls: 'primary' }] });
    } }],
  });
}

// ---------------------------------------------------------------- Settings

export async function settings(el) {
  const [s, st] = await Promise.all([api('/api/admin/settings'), api('/api/admin/storage')]);
  const vals = { ...s };
  const changed = {};
  const set = (k, v) => { vals[k] = v; changed[k] = v; saveBar.classList.remove('hidden'); };
  const numIn = (k, attrs = {}) => { const i = h('input', { class: 'input', type: 'number', value: vals[k], ...attrs }); i.addEventListener('input', () => set(k, +i.value)); return i; };
  const sel = (k, opts) => { const x = h('select', { class: 'input' }, opts.map(([v, l]) => h('option', { value: v, selected: String(vals[k]) === String(v) }, l))); x.addEventListener('change', () => set(k, isNaN(+x.value) || x.value === '' ? x.value : +x.value)); return x; };
  const chk = (k, label, hint) => { const c = h('input', { type: 'checkbox', checked: !!vals[k] }); c.addEventListener('change', () => set(k, c.checked)); return h('label', { class: 'opt' }, c, h('span', {}, h('b', { style: { fontWeight: 500 } }, label), hint ? h('div', { class: 'hint' }, hint) : null)); };
  const range = (k, min, max, step = 1, fmt = (v) => v) => {
    const out = h('span', { class: 'mono', style: { minWidth: '64px', textAlign: 'right' } }, fmt(Math.min(max, Math.max(min, vals[k]))));
    const r = h('input', { type: 'range', min, max, step, value: Math.min(max, Math.max(min, vals[k])) });
    r.addEventListener('input', () => { out.textContent = fmt(+r.value); set(k, +r.value); });
    return h('div', { class: 'row', style: { flexWrap: 'nowrap' } }, r, out);
  };
  const text = (k, placeholder) => { const i = h('input', { class: 'input mono', value: vals[k] || '', placeholder }); i.addEventListener('input', () => set(k, i.value)); return i; };
  const saveBar = h('div', { class: 'card hidden', style: { position: 'sticky', bottom: '16px', flexDirection: 'row', alignItems: 'center', borderColor: 'var(--accent)', zIndex: 5, marginTop: '20px' } },
    h('b', { class: 'grow' }, 'You have unsaved changes'),
    h('button', { class: 'btn ghost', onclick: () => go('#/admin/settings') }, 'Undo'),
    h('button', { class: 'btn primary', onclick: async () => {
      try {
        await api('/api/admin/settings', { method: 'PUT', body: changed });
        toast('Settings saved.');
        if ('allow_delete' in changed) state.allowDelete = changed.allow_delete;
        go('#/admin/settings');
      } catch (e) { errToast(e); }
    } }, 'Save changes'));
  const cpu = s._threads_available || 4;
  el.append(head('Settings'),
    h('div', { class: 'cards' },
      h('section', { class: 'card' }, h('h2', {}, 'Thumbnails'),
        h('span', { class: 'hint', style: { marginTop: '-10px' } }, 'Small copies made once so the gallery loads fast. Your originals are never changed.'),
        h('label', { class: 'field' }, 'Grid thumbnail size', h('span', { class: 'hint' }, 'Longest side, in pixels'), range('thumb_size', 200, 800, 20, v => `${v} px`)),
        h('label', { class: 'field' }, 'Full-screen preview size', sel('preview_size', [[1080, '1080 px'], [1440, '1440 px'], [2160, '2160 px (4K)']])),
        h('div', { class: 'row', style: { flexWrap: 'nowrap' } },
          h('label', { class: 'field grow' }, 'Format', sel('thumb_format', [['webp', 'WebP (smallest)'], ['jpeg', 'JPEG']])),
          h('label', { class: 'field grow' }, 'Quality', sel('thumb_quality', [[70, '70%'], [80, '80%'], [90, '90%']]))),
        h('span', { class: 'hint' }, 'Changing these rebuilds all thumbnails in the background.')),
      h('section', { class: 'card' }, h('h2', {}, 'Face recognition'),
        h('span', { class: 'hint', style: { marginTop: '-10px' } }, 'Runs on your server only. Nothing is sent to the internet.'),
        chk('faces_enabled', 'Turn on face recognition'),
        h('label', { class: 'field' }, 'How strict should matching be?', h('span', { class: 'hint' }, 'Stricter = fewer wrong matches, but more “Who is this?” groups. Changing it re-groups faces that don’t have a name yet.'),
          range('face_strictness', 0, 100, 5, v => v < 35 ? 'Looser' : v > 65 ? 'Stricter' : 'Balanced')),
        h('label', { class: 'field' }, 'Ignore faces smaller than', sel('face_min_size', [[24, '24 px (find tiny faces)'], [40, '40 px (recommended)'], [60, '60 px'], [100, '100 px (only big faces)']])),
        h('label', { class: 'field' }, 'Smallest group shown in “Needs a name”', sel('cluster_min_faces', [[2, '2 faces'], [3, '3 faces'], [5, '5 faces'], [10, '10 faces']])),
        h('div', { class: 'row' },
          h('button', { class: 'btn small', onclick: async () => { if (await confirmDialog({ title: 'Look for faces again?', text: 'Faces in groups without a name are thrown away and every photo is checked again. Named people are kept.', confirmLabel: 'Re-scan faces' })) { await post('/api/admin/jobs/rescan_faces'); toast('Re-scanning faces.'); } } }, 'Re-scan faces'),
          h('button', { class: 'btn small ghost', onclick: async () => { await post('/api/admin/jobs/recluster'); toast('Re-grouping faces.'); } }, 'Re-group now')),
        h('div', { class: 'row', style: { paddingTop: '12px', borderTop: '1px solid var(--line)' } },
          h('div', { class: 'grow' }, h('b', { style: { fontWeight: 500 } }, 'Start naming over'),
            h('div', { class: 'hint' }, 'Removes every name. Faces stay found and go back into unnamed groups. No photos are touched.')),
          h('button', { class: 'btn small danger', onclick: async () => {
            if (!await confirmDialog({ title: 'Remove all names and start over?', text: 'Every person loses their name and all faces go back into unnamed groups for you to name again. Faces don’t have to be found again, and no photos are changed. This can’t be undone.', confirmLabel: 'Remove all names', danger: true, typeWord: 'RESET' })) return;
            try { await post('/api/admin/jobs/forget_names', { confirm: 'RESET' }); toast('Removing names. The groups appear in Library › Faces in a moment.'); } catch (e) { errToast(e); }
          } }, 'Remove all names…')))),
    h('div', { class: 'cards', style: { marginTop: '20px' } },
      h('section', { class: 'card' }, h('h2', {}, 'Background speed'),
        h('span', { class: 'hint', style: { marginTop: '-10px' } }, 'Face finding, thumbnails and duplicate checks. They never stop, they just let your other apps go first.'),
        h('label', { class: 'field' }, 'CPU threads to use', h('span', { class: 'hint' }, `This server has ${cpu}`), range('worker_threads', 1, cpu, 1, v => `${v}`)),
        chk('night_boost', 'Use more threads overnight'),
        h('div', { class: 'row', style: { flexWrap: 'nowrap' } },
          h('label', { class: 'field grow' }, 'Overnight threads', numIn('night_threads', { min: 1, max: cpu })),
          h('label', { class: 'field grow' }, 'From (hour)', numIn('night_start', { min: 0, max: 23 })),
          h('label', { class: 'field grow' }, 'Until (hour)', numIn('night_end', { min: 0, max: 23 }))),
        h('label', { class: 'field' }, 'Look for new photos every', sel('scan_interval_hours', [[1, '1 hour'], [6, '6 hours'], [24, 'Day'], [0, 'Only when I click Scan']])),
        h('label', { class: 'field' }, 'Unraid container log shows', sel('log_detail', [['files', 'Every file as it’s handled (most detail)'], ['summary', 'A progress summary every minute']])),
        chk('paused', 'Pause all background work')),
      h('section', { class: 'card' }, h('h2', {}, 'Recycle Bin'),
        h('span', { class: 'hint', style: { marginTop: '-10px' } }, 'Deleted photos go here first, into a hidden .recycle folder inside each library (same drive, so it’s instant).'),
        chk('allow_delete', 'Let admins delete photos', 'Needs the library folders mapped as Read/Write in Unraid.'),
        h('label', { class: 'field' }, 'Empty automatically', sel('recycle_auto_days', [[0, 'Never (I’ll empty it myself)'], [30, 'After 30 days'], [90, 'After 90 days']])))),
    h('section', { class: 'card', style: { marginTop: '20px' } }, h('h2', {}, 'Storage locations'),
      h('span', { class: 'hint', style: { marginTop: '-10px' } }, 'Where EasyPhotos keeps what it makes. Point thumbnails at your array so your appdata drive doesn’t fill up.'),
      h('div', { class: 'cards' },
        h('label', { class: 'field', style: { flex: '1 1 300px' } }, 'Thumbnails & previews folder', h('span', { class: 'hint' }, 'Leave empty to use /thumbs (map it to an array share in Unraid).'), text('thumbs_dir', st.thumbs.path),
          h('span', { class: 'hint', style: { color: st.thumbs.exists ? 'var(--green)' : 'var(--orange)' } }, st.thumbs.exists ? `In use: ${st.thumbs.path} · ${st.thumbs.used !== null ? fmtBytes(st.thumbs.used) + ' used · ' : ''}${fmtBytes(st.thumbs.free)} free` : 'Folder not found')),
        h('label', { class: 'field', style: { flex: '1 1 300px' } }, 'Face pictures folder', h('span', { class: 'hint' }, 'Leave empty to keep them with the thumbnails.'), text('faces_dir', st.faces.path))),
      h('div', { class: 'row', style: { padding: '14px', border: '1px solid var(--line2)', borderRadius: '12px' } },
        icon('lock'),
        h('div', { class: 'grow' }, h('b', {}, st.encrypted ? 'Database: one encrypted file' : 'Database: one file'),
          h('div', { class: 'mono muted' }, `/config/easyphotos.db · ${fmtBytes(st.db_size)}`),
          h('div', { class: 'hint' }, st.encrypted ? 'Encrypted with your EP_DB_KEY. No MariaDB or Postgres needed.' : 'Not encrypted. Set EP_DB_KEY in the container on a fresh install to encrypt it.'),
          st.backups.length ? h('div', { class: 'hint' }, `Latest backup: ${st.backups[0]}`) : null),
        h('button', { class: 'btn', onclick: async () => { try { const r = await post('/api/admin/backup'); toast(`Backup saved: ${r.file}`); } catch (e) { errToast(e); } } }, 'Back up now'))),
    h('section', { class: 'card', style: { marginTop: '20px' } }, h('h2', {}, 'Optional extras'),
      h('span', { class: 'hint', style: { marginTop: '-10px' } }, 'Turn on only what you want. Anything that’s off uses no memory or CPU.'),
      h('div', { class: 'optgrid' },
        chk('duplicates_enabled', 'Duplicate finder', 'Spots copies while making thumbnails. Never deletes on its own.'),
        chk('faces_favorites_only', 'Only look for favorite people', 'Faces only asks about groups that look like people you starred, and new photos are only matched to them. Nobody else is deleted; turn this off to see everyone again.'),
        chk('quality_enabled', 'Blurry photo finder', 'Checks each photo for blur and darkness in the background (quick). Never deletes on its own.'),
        chk('places_enabled', 'Place names (offline)', 'Turns GPS into “Whiteland, Indiana” with a built-in town list. No internet.'),
        chk('xmp_enabled', 'Save names, tags and descriptions to .xmp files', 'Small file next to each photo so your work goes with you to any app. Needs Read/Write access.'),
        chk('video_compat', 'Convert videos only when needed', 'Plays originals. Converts on the fly only if a browser can’t play one.'))),
    saveBar);
}

void num; void promptDialog;
