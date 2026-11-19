// Admin pages.
import { h, icon, api, thumbUrl, faceUrl, img, toast, errToast, modal, confirmDialog, promptDialog, fmtBytes, fmtAgo, fmtEta, num, fmtDateTime } from './ui.js';
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
          h('span', { class: 'act-kind' }, { scan: 'Scan', thumbs: 'Thumbnail', faces: 'Faces', duplicates: 'Duplicates', command: 'You' }[r.kind] || r.kind),
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

export async function faces(el, p, q) {
  let tab = q.tab || 'needs', sort = 'count', offset = 0;
  const tabs = h('div', { class: 'tabs' });
  const grid = h('div', { class: 'fgrid' });
  const more = h('div', { style: { textAlign: 'center', padding: '16px' } });
  el.append(head('Library › Faces', 'Each card is one person the AI grouped together. Type a name, or leave it unmatched. Click a card to fix wrong faces in it.', [
    h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Sort', onchange: (e) => { sort = e.target.value; load(true); } },
      h('option', { value: 'count' }, 'Most photos first'), h('option', { value: 'new' }, 'Newest first')),
  ]), tabs, h('div', { style: { height: '16px' } }), grid, more);
  const names = { needs: 'Needs a name', named: 'Named', unmatched: 'Left unmatched', single: 'Single faces', hidden: 'Hidden' };
  async function load(reset = false) {
    if (reset) { offset = 0; grid.innerHTML = ''; }
    let r;
    try { r = await api(`/api/admin/faces?tab=${tab}&sort=${sort}&offset=${offset}&limit=40`); } catch (e) { return errToast(e); }
    tabs.innerHTML = '';
    Object.entries(names).forEach(([k, label]) => tabs.append(h('button', { class: tab === k ? 'on' : '', onclick: () => { tab = k; history.replaceState(null, '', `#/admin/faces?tab=${k}`); load(true); } }, `${label} · ${num(r.counts[k])}`)));
    if (!r.items.length && !offset) {
      grid.append(h('div', { class: 'empty', style: { gridColumn: '1/-1' } }, icon('scan'),
        tab === 'needs' ? 'Nothing waiting for a name. If face finding is still running, more will show up here.' : 'Nothing here.'));
    }
    r.items.forEach(item => grid.append(card(item)));
    offset += r.items.length;
    more.innerHTML = '';
    if (r.items.length === 40) more.append(h('button', { class: 'btn', onclick: () => load() }, 'Show more'));
  }
  function card(item) {
    const input = h('input', { class: 'input', placeholder: 'Type a name…', value: item.name || (item.suggest ? item.suggest.name : ''), list: 'people-names', 'aria-label': 'Name' });
    const c = h('article', { class: 'fcard' });
    const save = async () => {
      const name = input.value.trim();
      if (!name) { toast('Type a name first.', true); return; }
      try {
        const r = await api(`/api/people/${item.id}`, { method: 'PATCH', body: { name } });
        toast(r.merged ? `Added to ${name}.` : `Saved as ${name}.`);
        if (tab !== 'named') c.remove();
      } catch (e) { errToast(e); }
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
    c.append(
      h('div', { class: 'top', onclick: () => go(`#/admin/faces/${item.id}`) },
        item.cover ? h('img', { class: 'cover', src: faceUrl(item.cover), alt: '' }) : h('div', { class: 'cover' }),
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 } },
          h('b', {}, item.name || `${num(item.photos)} photo${item.photos === 1 ? '' : 's'}`),
          h('span', { class: 'hint' }, item.name ? `${num(item.photos)} photos` : item.years),
          item.suggest && !item.name ? h('span', { class: 'suggest-line' }, `Looks like ${item.suggest.name}?`) : null)),
      item.samples.length ? h('div', { class: 'samples' }, item.samples.map(s => h('img', { src: faceUrl(s), alt: '', loading: 'lazy' }))) : null,
      input,
      h('div', { class: 'row', style: { gap: '6px', flexWrap: 'nowrap' } },
        h('button', { class: 'btn small primary grow', onclick: save }, item.name ? 'Rename' : 'Save name'),
        tab === 'unmatched' || tab === 'hidden'
          ? h('button', { class: 'btn small grow', onclick: async () => { await api(`/api/people/${item.id}`, { method: 'PATCH', body: { unmatched: false, hidden: false } }); c.remove(); } }, 'Move back')
          : !item.name ? h('button', { class: 'btn small grow', onclick: async () => { await api(`/api/people/${item.id}`, { method: 'PATCH', body: { unmatched: true } }); toast('Left unmatched.'); c.remove(); } }, 'Leave unmatched') : null));
    return c;
  }
  const dl = h('datalist', { id: 'people-names' });
  api('/api/people').then(ps => ps.forEach(pp => dl.append(h('option', { value: pp.name })))).catch(() => {});
  el.append(dl);
  load(true);
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
      try { const r = await api(`/api/people/${p.id}`, { method: 'PATCH', body: { name } }); toast(r.merged ? `Merged into ${name}.` : 'Saved.'); go(`#/admin/faces/${r.id}`); } catch (e) { errToast(e); }
    } }, icon('edit'), pr.name ? 'Rename' : 'Name'),
    pr.name ? h('a', { class: 'btn', href: `#/people/${p.id}` }, 'See photos') : null,
  ].filter(Boolean)), bar, grid);
  const drawBar = () => {
    bar.innerHTML = '';
    if (!sel.size) { bar.append(h('span', { class: 'hint' }, 'Select faces to move them to someone else or mark them as “not a face”.')); return; }
    bar.append(h('b', {}, `${sel.size} selected`),
      h('button', { class: 'btn small', onclick: async () => {
        const name = await promptDialog({ title: 'Move to…', label: 'Who are they?', placeholder: 'Type a name' });
        if (!name) return;
        for (const f of sel) await api(`/api/faces/${f}`, { method: 'PATCH', body: { name } });
        toast(`Moved to ${name}.`); reload();
      } }, 'Move to someone else'),
      h('button', { class: 'btn small', onclick: async () => { for (const f of sel) await api(`/api/faces/${f}`, { method: 'PATCH', body: { unassign: true } }); toast('Removed from this person.'); reload(); } }, 'Remove from this person'),
      h('button', { class: 'btn small ghost', onclick: async () => { for (const f of sel) await api(`/api/faces/${f}`, { method: 'PATCH', body: { reject: true } }); toast('Marked as not a face.'); reload(); } }, 'Not a face'),
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
  let kind = q.kind || 'exact', autoPicked = false;
  const tabs = h('div', { class: 'tabs' });
  const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '18px', marginTop: '18px' } });
  el.append(head('Library › Duplicates', 'Nothing happens to your files until you pick an action and confirm it.', [
    h('button', { class: 'btn', onclick: async () => { await post('/api/admin/jobs/duplicates'); toast('Checking for duplicates again…'); } }, 'Check again')]), tabs, list);
  async function load() {
    let r;
    try { r = await api(`/api/admin/duplicates?kind=${kind === 'kept' ? 'exact' : kind}&status=${kind === 'kept' ? 'kept' : 'open'}&limit=20`); } catch (e) { return errToast(e); }
    const c = r.counts;
    if (!q.kind && !autoPicked && kind === 'exact' && !c['exact:open'] && c['similar:open']) { autoPicked = true; kind = 'similar'; return load(); }
    tabs.innerHTML = '';
    [['exact', `Exact copies · ${num(c['exact:open'] || 0)}`], ['similar', `Look the same · ${num(c['similar:open'] || 0)}`], ['kept', `Kept both · ${num((c['exact:kept'] || 0) + (c['similar:kept'] || 0))}`]]
      .forEach(([k, label]) => tabs.append(h('button', { class: kind === k ? 'on' : '', onclick: () => { kind = k; load(); } }, label)));
    tabs.append(h('span', { class: 'hint', style: { marginLeft: 'auto', alignSelf: 'center' } }, kind === 'similar' ? '“Look the same” are resized or re-saved copies. Check them by eye.' : ''));
    list.innerHTML = '';
    if (!r.groups.length) list.append(h('div', { class: 'empty' }, icon('dup'), kind === 'kept' ? 'No groups marked “keep both”.' : 'No duplicates found. 🎉'.replace(' 🎉', '')));
    r.groups.forEach((g, gi) => list.append(group(g, gi)));
  }
  function group(g, gi) {
    let keep = g.suggest_keep;
    const sec = h('section', { class: 'card' });
    const copies = h('div', { class: 'dupgrid' });
    const draw = () => {
      copies.innerHTML = '';
      g.members.forEach(m => {
        const isKeep = m.id === keep;
        copies.append(h('div', { class: 'dupcopy' + (isKeep ? ' keep' : ''), onclick: () => { keep = m.id; draw(); }, role: 'radio', 'aria-checked': isKeep ? 'true' : 'false', tabindex: 0 },
          h('div', { class: 'img' }, img(thumbUrl(m.id, m.thumb_ver)), h('span', { class: 'tag' }, isKeep ? (m.id === g.suggest_keep ? 'Keep (suggested)' : 'Keep') : 'Extra copy')),
          h('div', { class: 'mono muted', style: { wordBreak: 'break-all' } }, `${m.library} / ${m.path}`),
          h('div', { style: { fontSize: '13px', color: 'var(--text2)' } }, `${m.width || '?'} × ${m.height || '?'} · ${fmtBytes(m.size)}`),
          h('div', { class: 'hint' }, [m.named_faces ? `${m.named_faces} named face${m.named_faces > 1 ? 's' : ''}` : null, m.tags ? `${m.tags} tags` : null, m.albums ? 'in an album' : null, m.lat ? 'has location' : null].filter(Boolean).join(' · ') || 'No extra info')));
      });
    };
    draw();
    const others = () => g.members.length - 1;
    sec.append(
      h('div', { class: 'row' }, h('b', {}, `Group ${gi + 1}`), h('span', { class: 'badge green' }, g.kind === 'exact' ? '100% identical' : 'Look the same'),
        h('span', { class: 'hint' }, fmtDateTime(g.members[0]?.taken_at).date)),
      copies,
      kind === 'kept' ? h('div', { class: 'row' }, h('button', { class: 'btn', onclick: async () => { await post(`/api/admin/duplicates/${g.id}`, { action: 'reopen' }); sec.remove(); } }, 'Check this group again'))
        : h('div', { class: 'row' },
          h('button', { class: 'btn primary', onclick: async () => {
            try { await post(`/api/admin/duplicates/${g.id}`, { action: 'hide', keep }); toast('Extra copy hidden. The file was not touched.'); sec.remove(); } catch (e) { errToast(e); }
          } }, `Hide the extra cop${others() > 1 ? 'ies' : 'y'}`),
          state.allowDelete ? h('button', { class: 'btn warn', onclick: async () => {
            if (!await confirmDialog({ title: `Delete ${others()} photo${others() > 1 ? 's' : ''}?`, text: 'The extra copy goes to the Recycle Bin. You can restore it until you empty the bin.', confirmLabel: 'Send to Recycle Bin', danger: true, typeWord: 'DELETE' })) return;
            try {
              const r = await post(`/api/admin/duplicates/${g.id}`, { action: 'delete', keep, confirm: 'DELETE' });
              if (r.errors && r.errors.length) toast(r.errors[0], true); else { toast('Moved to the Recycle Bin.'); sec.remove(); }
            } catch (e) { errToast(e); }
          } }, `Delete extra cop${others() > 1 ? 'ies' : 'y'}…`) : null,
          h('button', { class: 'btn ghost', onclick: async () => { await post(`/api/admin/duplicates/${g.id}`, { action: 'keep_both' }); toast('Kept both.'); sec.remove(); } }, 'Keep both'),
          h('button', { class: 'btn ghost', style: { marginLeft: 'auto' }, onclick: () => sec.remove() }, 'Skip')),
      h('span', { class: 'hint' }, 'Click a copy to choose which one to keep. “Hide” only hides it in EasyPhotos. “Delete” sends it to the Recycle Bin.'));
    return sec;
  }
  load();
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
    try { const r = await post('/api/admin/recycle/empty', { confirm: 'EMPTY' }); toast(`${r.purged} deleted forever.`); load(); } catch (e) { errToast(e); }
  } }, 'Empty Recycle Bin…')]);
  el.append(headEl, body);
  async function load() {
    sel.clear(); restoreBtn.disabled = true;
    const r = await api('/api/admin/recycle?limit=500');
    headEl.querySelector('.sub').textContent = `${num(r.count)} photos · ${fmtBytes(r.bytes)}. Restore puts each one back in its original folder.`;
    body.innerHTML = '';
    if (!r.items.length) { body.append(h('div', { class: 'empty' }, icon('trash'), 'The Recycle Bin is empty.')); return; }
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
          h('button', { class: 'btn small ghost', onclick: async () => { await post('/api/admin/jobs/recluster'); toast('Re-grouping faces.'); } }, 'Re-group now')))),
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
        chk('places_enabled', 'Place names (offline)', 'Turns GPS into “Whiteland, Indiana” with a built-in town list. No internet.'),
        chk('xmp_enabled', 'Save names, tags and descriptions to .xmp files', 'Small file next to each photo so your work goes with you to any app. Needs Read/Write access.'),
        chk('video_compat', 'Convert videos only when needed', 'Plays originals. Converts on the fly only if a browser can’t play one.'))),
    saveBar);
}

void num; void promptDialog;
