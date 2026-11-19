// Full-screen photo viewer with details, faces, editing (admin) and face drawing.
import { h, icon, api, previewUrl, faceUrl, fmtDateTime, fmtBytes, fmtDuration, placeText, toast, errToast, modal, confirmDialog, download, MONTHS_LONG } from './ui.js';
import { state, loadLeaflet, TILE_URL, TILE_ATTR } from './state.js';
import { go } from './router.js';

let current = null;

export function openViewer(opts) {
  if (current) current.close();
  current = new Viewer(opts);
  current.open();
}

class Viewer {
  constructor({ items, index, grid }) {
    this.items = items;
    this.index = index;
    this.grid = grid;
    this.showInfo = window.innerWidth > 1100 && localStorage.getItem('ep_info') !== '0';
    this.showFaces = localStorage.getItem('ep_faces') !== '0';
    this.mode = 'info'; // info | edit
    this.drawing = false;
    this.detail = null;
    this.onKey = this.onKey.bind(this);
  }

  open() {
    this.prevScroll = window.scrollY;
    this.root = h('div', { class: 'viewer', role: 'dialog', 'aria-label': 'Photo viewer' });
    document.body.append(this.root);
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', this.onKey);
    history.pushState({ viewer: true }, '');
    this._pop = () => this.close(true);
    window.addEventListener('popstate', this._pop);
    this.show();
  }

  close(fromPop = false) {
    document.removeEventListener('keydown', this.onKey);
    window.removeEventListener('popstate', this._pop);
    this.root.remove();
    document.body.style.overflow = '';
    current = null;
    if (!fromPop && history.state && history.state.viewer) history.back();
  }

  onKey(e) {
    if (e.target.closest('input,textarea,select')) return;
    if (document.querySelector('.modal-back')) return;
    if (e.key === 'Escape') { if (this.drawing) this.setDrawing(false); else this.close(); }
    else if (e.key === 'ArrowRight') this.step(1);
    else if (e.key === 'ArrowLeft') this.step(-1);
    else if (e.key === 'i') this.toggleInfo();
  }

  async step(d) {
    const ni = this.index + d;
    if (ni < 0) return;
    if (ni >= this.items.length) {
      if (this.grid && !this.grid.done) { await this.grid.loadMore(); if (ni >= this.items.length) return; }
      else return;
    }
    this.index = ni;
    this.mode = 'info';
    this.setDrawing(false);
    this.show();
  }

  get item() { return this.items[this.index]; }

  async show() {
    const it = this.item;
    this.detail = null;
    this.root.innerHTML = '';
    this.root.classList.toggle('info-open', this.showInfo);
    const u = state.user;
    const favBtn = h('button', { class: 'iconbtn', 'aria-label': 'Favorite', onclick: () => this.toggleFav() }, icon('heart'));
    this.favBtn = favBtn;
    this.facesBtn = h('button', { class: 'iconbtn hide-m' + (this.showFaces ? ' on' : ''), 'aria-label': 'Show face boxes', title: 'Show face boxes', onclick: () => {
      this.showFaces = !this.showFaces; localStorage.setItem('ep_faces', this.showFaces ? '1' : '0');
      this.facesBtn.classList.toggle('on', this.showFaces); this.drawFaces();
    } }, icon('scan'));
    this.when = h('div', { class: 'when' }, h('span', {}, ''), h('span', {}, ''));
    const top = h('div', { class: 'vtop' },
      h('button', { class: 'iconbtn', 'aria-label': 'Close', onclick: () => this.close() }, icon('back')),
      this.when,
      h('div', { class: 'acts' },
        favBtn,
        it.k !== 'v' ? this.facesBtn : null,
        u.can_download ? h('button', { class: 'iconbtn', 'aria-label': 'Download original', title: 'Download original', onclick: () => download(`/api/assets/${it.id}/original`) }, icon('down')) : null,
        u.is_admin ? h('button', { class: 'iconbtn', 'aria-label': 'Edit info and faces', title: 'Edit', onclick: () => { this.mode = 'edit'; this.showInfo = true; this.root.classList.add('info-open'); this.renderSide(); } }, icon('edit')) : null,
        u.is_admin && state.allowDelete ? h('button', { class: 'iconbtn hide-m', 'aria-label': 'Delete', title: 'Delete', onclick: () => this.del() }, icon('trash')) : null,
        h('button', { class: 'iconbtn' + (this.showInfo ? ' on' : ''), 'aria-label': 'Details', title: 'Details (i)', onclick: () => this.toggleInfo() }, icon('info'))));
    this.media = h('div', { class: 'media' });
    this.frame = h('div', { class: 'frame' });
    this.media.append(this.frame);
    this.media.append(
      h('button', { class: 'iconbtn nav prev', 'aria-label': 'Previous photo', onclick: () => this.step(-1) }, icon('back')),
      h('button', { class: 'iconbtn nav next', 'aria-label': 'Next photo', onclick: () => this.step(1) }, icon('next')));
    this.stage = h('div', { class: 'stage' }, top, this.media);
    this.side = h('aside', { class: 'info' + (this.showInfo ? '' : ' hidden'), 'aria-label': 'Photo details' });
    this.root.append(this.stage, this.side);
    this.swipe();
    if (it.k === 'v') {
      const v = h('video', { controls: true, autoplay: true, playsinline: true, poster: previewUrl(it.id, it.v), preload: 'metadata' });
      if (!u.can_download) v.setAttribute('controlslist', 'nodownload');
      let triedCompat = false;
      v.addEventListener('error', () => {
        if (triedCompat) { toast('This video could not be played.', true); return; }
        triedCompat = true;
        toast('Converting this video so your browser can play it…');
        v.src = `/api/assets/${it.id}/video?compat=1`;
      });
      v.src = `/api/assets/${it.id}/video`;
      this.frame.append(v);
    } else {
      this.imgEl = h('img', { class: 'main', alt: '', src: previewUrl(it.id, it.v), draggable: 'false' });
      this.imgEl.addEventListener('load', () => this.drawFaces());
      this.frame.append(this.imgEl);
      if (it.l) {
        const liveBtn = h('button', { class: 'btn small', style: { position: 'absolute', left: '10px', top: '10px', zIndex: 2, background: 'rgba(0,0,0,.55)' } }, icon('live'), 'LIVE');
        liveBtn.addEventListener('click', () => this.playLive());
        this.frame.append(liveBtn);
      }
    }
    try {
      this.detail = await api(`/api/assets/${it.id}`);
    } catch (e) { errToast(e); return; }
    if (this.item !== it) return;
    const dtx = fmtDateTime(this.detail.taken_at);
    this.when.children[0].textContent = dtx.date;
    this.when.children[1].textContent = dtx.time;
    favBtn.classList.toggle('on', this.detail.favorite);
    favBtn.querySelector('svg').style.fill = this.detail.favorite ? '#F27A8A' : 'none';
    favBtn.querySelector('svg').style.stroke = this.detail.favorite ? '#F27A8A' : 'currentColor';
    this.drawFaces();
    this.renderSide();
    // Preload neighbours
    [1, -1].forEach(d => { const n = this.items[this.index + d]; if (n && n.k !== 'v') { const i = new Image(); i.src = previewUrl(n.id, n.v); } });
  }

  playLive() {
    const it = this.item;
    const v = h('video', { autoplay: true, playsinline: true, muted: false, style: { position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', background: '#000' } });
    let compat = false;
    v.addEventListener('error', () => { if (!compat) { compat = true; v.src = `/api/assets/${it.id}/live?compat=1`; } });
    v.addEventListener('ended', () => v.remove());
    v.src = `/api/assets/${it.id}/live`;
    this.frame.append(v);
  }

  swipe() {
    let x0 = null, y0 = null;
    this.media.addEventListener('touchstart', (e) => { if (this.drawing) return; x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
    this.media.addEventListener('touchend', (e) => {
      if (x0 === null) return;
      const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      x0 = null;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) this.step(dx < 0 ? 1 : -1);
      else if (dy < -80 && Math.abs(dy) > Math.abs(dx)) { this.showInfo = true; this.root.classList.add('info-open'); this.side.classList.remove('hidden'); }
      else if (dy > 100 && this.showInfo) { this.toggleInfo(); }
    }, { passive: true });
  }

  toggleInfo() {
    this.showInfo = !this.showInfo;
    if (window.innerWidth > 900) localStorage.setItem('ep_info', this.showInfo ? '1' : '0');
    this.root.classList.toggle('info-open', this.showInfo);
    this.side.classList.toggle('hidden', !this.showInfo);
    if (!this.showInfo) this.mode = 'info';
    else this.renderSide();
    setTimeout(() => this.drawFaces(), 50);
  }

  drawFaces() {
    if (!this.frame) return;
    this.frame.querySelectorAll('.facebox').forEach(b => b.remove());
    if (!this.detail || !this.showFaces || this.item.k === 'v' || !this.imgEl) return;
    for (const f of this.detail.faces) {
      const b = h('div', { class: 'facebox' + (f.name ? '' : ' unknown'), style: {
        left: `${f.x1 * 100}%`, top: `${f.y1 * 100}%`, width: `${(f.x2 - f.x1) * 100}%`, height: `${(f.y2 - f.y1) * 100}%` } },
        h('span', {}, f.name || (state.user.is_admin ? 'Who is this?' : 'Unknown')));
      if (state.user.is_admin) { b.style.cursor = 'pointer'; b.addEventListener('click', () => this.nameFace(f)); }
      this.frame.append(b);
    }
  }

  async toggleFav() {
    if (!this.detail) return;
    try {
      const r = await api(`/api/assets/${this.item.id}/favorite`, { method: 'POST', body: { favorite: !this.detail.favorite } });
      this.detail.favorite = r.favorite;
      this.item.f = r.favorite ? 1 : 0;
      const s = this.favBtn.querySelector('svg');
      s.style.fill = r.favorite ? '#F27A8A' : 'none';
      s.style.stroke = r.favorite ? '#F27A8A' : 'currentColor';
    } catch (e) { errToast(e); }
  }

  async del() {
    const ok = await confirmDialog({ title: 'Delete this photo?', text: 'It will go to the Recycle Bin. You can restore it until you empty the bin.', confirmLabel: 'Send to Recycle Bin', danger: true });
    if (!ok) return;
    try {
      const res = await api('/api/assets/bulk', { method: 'POST', body: { ids: [this.item.id], action: 'delete' } });
      if (res.errors && res.errors.length) { toast(res.errors[0], true); return; }
      toast('Moved to the Recycle Bin.');
      const id = this.item.id;
      this.items.splice(this.index, 1);
      if (this.grid) this.grid.render();
      if (!this.items.length) return this.close();
      if (this.index >= this.items.length) this.index = this.items.length - 1;
      this.show();
      void id;
    } catch (e) { errToast(e); }
  }

  // ------------------------------------------------------------ side panel
  renderSide() {
    if (!this.side) return;
    this.side.classList.toggle('hidden', !this.showInfo);
    if (!this.showInfo || !this.detail) return;
    this.side.innerHTML = '';
    if (this.mode === 'edit' && state.user.is_admin) return this.renderEdit();
    const d = this.detail;
    const dtx = fmtDateTime(d.taken_at);
    const irow = (ic, main, sub) => h('div', { class: 'irow' }, icon(ic), h('div', {}, main, sub ? h('div', { class: 'sub' }, sub) : null));
    const camera = [d.camera_make && !(d.camera_model || '').startsWith(d.camera_make) ? d.camera_make : null, d.camera_model].filter(Boolean).join(' ');
    const exif = [d.fnumber ? `ƒ/${d.fnumber}` : null, d.exposure ? `${d.exposure}s` : null, d.focal ? `${d.focal}mm` : null, d.iso ? `ISO ${d.iso}` : null].filter(Boolean).join(' · ');
    const named = d.faces.filter(f => f.name || state.user.is_admin);
    this.side.append(
      h('h2', {}, 'Details'),
      d.description ? h('p', { style: { margin: 0, fontSize: '15px' } }, d.description) : null,
      irow('calendar', dtx.date, dtx.time + (d.date_source === 'manual' ? ' · fixed by admin' : d.date_source === 'file' ? ' · from file date' : '')),
      camera || exif ? irow('camera', camera || 'Camera', exif) : null,
      irow('file', d.filename, [d.width && d.height ? `${d.width} × ${d.height}` : null, fmtBytes(d.size), d.duration ? fmtDuration(d.duration) : null, d.library].filter(Boolean).join(' · ')),
      named.length ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
        h('div', { style: { fontSize: '13px', fontWeight: 600, color: 'var(--text2)' } }, 'People in this photo'),
        h('div', { class: 'facelist' }, named.map(f => f.person_id && f.name
          ? h('a', { href: `#/people/${f.person_id}`, onclick: () => this.close() }, h('img', { src: faceUrl(f.id), alt: '' }), f.name)
          : h('div', { class: 'unk', style: { cursor: 'pointer' }, onclick: () => this.nameFace(f) }, h('img', { src: faceUrl(f.id), alt: '' }), 'Who is this?')))) : null,
      d.tags.length ? h('div', { class: 'row', style: { gap: '6px' } }, icon('tag'), d.tags.map(t => h('a', { class: 'chip', href: `#/tags/${t.id}`, onclick: () => this.close() }, t.name))) : null,
      d.albums.length ? h('div', { class: 'row', style: { gap: '6px' } }, icon('album'), d.albums.map(a => h('a', { class: 'chip', href: `#/albums/${a.id}`, onclick: () => this.close() }, a.name))) : null,
      placeText(d) || d.lat ? irow('map', placeText(d) || 'Location', d.lat ? `${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}` : null) : null,
      d.lat ? this.minimap(d.lat, d.lon) : null,
      state.user.is_admin && d.path ? h('div', { class: 'mono muted', style: { wordBreak: 'break-all' } }, d.path) : null,
    );
  }

  minimap(lat, lon) {
    const box = h('div', { class: 'minimap' });
    loadLeaflet().then(L => {
      const m = L.map(box, { zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false, touchZoom: false, keyboard: false }).setView([lat, lon], 12);
      L.tileLayer(TILE_URL, { maxZoom: 18 }).addTo(m);
      L.circleMarker([lat, lon], { radius: 8, color: '#0E1014', weight: 2, fillColor: '#6E9BFF', fillOpacity: 1 }).addTo(m);
      setTimeout(() => m.invalidateSize(), 50);
      box.addEventListener('click', () => { this.close(); go(`#/map?lat=${lat}&lon=${lon}`); });
    }).catch(() => box.remove());
    return box;
  }

  // ------------------------------------------------------------ editing
  renderEdit() {
    const d = this.detail;
    const [datePart, timePart] = (d.taken_at || 'T').split('T');
    const date = h('input', { class: 'input', type: 'date', value: datePart || '' });
    const time = h('input', { class: 'input', type: 'time', value: (timePart || '').slice(0, 5) });
    const desc = h('textarea', { class: 'input', rows: 2, placeholder: 'What’s happening in this photo?' }, d.description || '');
    const camera = h('input', { class: 'input', value: d.camera_model || '' });
    let loc = { lat: d.lat, lon: d.lon, changed: false, clear: false };
    const locText = h('div', { class: 'hint' }, placeText(d) || (d.lat ? `${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}` : 'No location'));
    const tags = d.tags.map(t => t.name);
    const chips = h('div', { class: 'tagchips' });
    const tagInput = h('input', { placeholder: 'Add a tag…', 'aria-label': 'Add a tag', list: 'edit-taglist' });
    const tagList = h('datalist', { id: 'edit-taglist' });
    api('/api/tags').then(ts => ts.forEach(t => tagList.append(h('option', { value: t.name })))).catch(() => {});
    const drawChips = () => {
      chips.innerHTML = '';
      tags.forEach((t, i) => chips.append(h('span', { class: 't' }, t, h('button', { 'aria-label': `Remove tag ${t}`, onclick: () => { tags.splice(i, 1); drawChips(); } }, '×'))));
      chips.append(tagInput);
    };
    tagInput.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ',') && tagInput.value.trim()) {
        e.preventDefault();
        const v = tagInput.value.trim().replace(/,$/, '');
        if (!tags.some(t => t.toLowerCase() === v.toLowerCase())) tags.push(v);
        tagInput.value = ''; drawChips(); tagInput.focus();
      } else if (e.key === 'Backspace' && !tagInput.value && tags.length) { tags.pop(); drawChips(); tagInput.focus(); }
    });
    drawChips();
    const save = async () => {
      if (tagInput.value.trim()) tags.push(tagInput.value.trim());
      const body = { description: desc.value, tags, camera_model: camera.value };
      if (date.value) body.taken_at = `${date.value}T${time.value || '12:00'}`;
      if (loc.clear) body.clear_location = true;
      else if (loc.changed) { body.lat = loc.lat; body.lon = loc.lon; }
      try {
        this.detail = await api(`/api/assets/${this.item.id}`, { method: 'PATCH', body });
        toast('Saved.');
        this.mode = 'info';
        this.show();
      } catch (e) { errToast(e); }
    };
    const facesBox = h('div', { class: 'edit-faces', style: { display: 'flex', flexDirection: 'column', gap: '8px' } });
    for (const f of d.faces) {
      facesBox.append(h('div', { class: 'ef' },
        h('img', { src: faceUrl(f.id), alt: '' }),
        h('span', { class: 'grow' }, f.name || 'Unknown', f.manual ? h('span', { class: 'hint' }, ' (drawn by you)') : null),
        h('button', { class: 'btn small ghost', style: { border: 0, color: 'var(--accent2)' }, onclick: () => this.nameFace(f) }, 'Change'),
        h('button', { class: 'iconbtn', 'aria-label': 'Remove this face', style: { width: '36px', height: '36px' }, onclick: async () => {
          try { await api(`/api/faces/${f.id}`, { method: 'PATCH', body: { reject: true } }); await this.refreshDetail(); } catch (e) { errToast(e); }
        } }, icon('x'))));
    }
    this.side.append(
      h('h2', {}, 'Photo info'),
      h('div', { class: 'row', style: { flexWrap: 'nowrap' } }, h('label', { class: 'field grow' }, 'Date', date), h('label', { class: 'field grow' }, 'Time', time)),
      h('label', { class: 'field' }, 'Location', locText,
        h('div', { class: 'row' },
          h('button', { class: 'btn small', type: 'button', onclick: () => pickOnMap(loc.lat, loc.lon).then(p => { if (p) { loc = { ...p, changed: true, clear: false }; locText.textContent = `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)} (place name fills in after saving)`; } }) }, icon('map'), 'Pick on map'),
          d.lat || d.city ? h('button', { class: 'btn small ghost', type: 'button', onclick: () => { loc.clear = true; locText.textContent = 'Location will be removed'; } }, 'Remove') : null)),
      h('label', { class: 'field' }, 'Description', desc),
      h('label', { class: 'field' }, h('span', {}, 'Tags ', h('span', { class: 'hint' }, 'Only the ones you add. Press Enter after each.')), chips, tagList),
      h('label', { class: 'field' }, 'Camera', camera),
      d.kind !== 'video' ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } },
        h('div', { class: 'row' }, h('b', { class: 'grow', style: { fontSize: '13px', color: 'var(--text2)' } }, 'Faces in this photo'),
          h('button', { class: 'btn small warn', onclick: () => this.setDrawing(!this.drawing) }, icon('box'), this.drawing ? 'Cancel drawing' : 'Draw face box')),
        this.drawing ? h('div', { class: 'hint' }, 'Drag a square around the face on the photo.') : null,
        facesBox) : null,
      h('div', { class: 'row', style: { marginTop: '6px' } },
        h('button', { class: 'btn primary grow', onclick: save }, 'Save changes'),
        h('button', { class: 'btn ghost', onclick: () => { this.mode = 'info'; this.setDrawing(false); this.renderSide(); } }, 'Cancel')),
      h('span', { class: 'hint' }, 'Changes are saved in EasyPhotos. Your original file isn’t touched.'),
    );
  }

  async refreshDetail() {
    this.detail = await api(`/api/assets/${this.item.id}`);
    this.drawFaces();
    this.renderSide();
  }

  setDrawing(on) {
    this.drawing = on;
    if (!this.media) return;
    this.media.classList.toggle('drawing', on);
    if (on && !this._drawBound) this.bindDraw();
    if (this.mode === 'edit') this.renderSide();
  }

  bindDraw() {
    this._drawBound = true;
    let start = null, box = null;
    const pos = (e) => {
      const r = this.imgEl.getBoundingClientRect();
      const p = e.touches ? e.touches[0] : e;
      return { x: Math.max(0, Math.min(1, (p.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (p.clientY - r.top) / r.height)) };
    };
    const down = (e) => {
      if (!this.drawing || !this.imgEl) return;
      e.preventDefault();
      start = pos(e);
      box = h('div', { class: 'drawbox' });
      this.frame.append(box);
    };
    const move = (e) => {
      if (!start || !box) return;
      e.preventDefault();
      const p = pos(e);
      const x1 = Math.min(start.x, p.x), y1 = Math.min(start.y, p.y);
      Object.assign(box.style, { left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${Math.abs(p.x - start.x) * 100}%`, height: `${Math.abs(p.y - start.y) * 100}%` });
      box._r = { x1, y1, x2: Math.max(start.x, p.x), y2: Math.max(start.y, p.y) };
    };
    const up = () => {
      if (!start || !box) return;
      const r = box._r;
      start = null;
      if (!r || r.x2 - r.x1 < 0.01 || r.y2 - r.y1 < 0.01) { box.remove(); box = null; return; }
      const b = box; box = null;
      this.askName('New face you marked', '', async (name, personId) => {
        await api(`/api/assets/${this.item.id}/faces`, { method: 'POST', body: { ...r, name, person_id: personId } });
        toast('Face added. It also helps EasyPhotos recognise this person.');
        b.remove();
        this.setDrawing(false);
        await this.refreshDetail();
      }, () => b.remove());
    };
    this.media.addEventListener('mousedown', down);
    this.media.addEventListener('touchstart', down, { passive: false });
    window.addEventListener('mousemove', move);
    this.media.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', up);
    this.media.addEventListener('touchend', up);
  }

  nameFace(f) {
    if (!state.user.is_admin) return;
    this.askName(f.name ? `Change who this is` : 'Who is this?', f.name || '', async (name, personId) => {
      await api(`/api/faces/${f.id}`, { method: 'PATCH', body: personId ? { person_id: personId } : { name } });
      toast('Saved.');
      await this.refreshDetail();
    }, null, f.id);
  }

  askName(title, value, onSave, onCancel, faceId = null) {
    const input = h('input', { class: 'input', value, placeholder: 'Type a name', autocomplete: 'off' });
    const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '2px' } });
    let chosen = null;
    const refresh = async () => {
      chosen = null;
      list.innerHTML = '';
      const q = input.value.trim();
      if (!q) return;
      try {
        const res = await api('/api/search/suggest?q=' + encodeURIComponent(q));
        res.people.forEach(p => list.append(h('button', { class: 'btn ghost', style: { justifyContent: 'flex-start', border: 0 }, onclick: () => { chosen = p.id; input.value = p.name; list.innerHTML = ''; } },
          p.cover_face_id ? h('img', { src: faceUrl(p.cover_face_id), style: { width: '28px', height: '28px', borderRadius: '50%', objectFit: 'cover' }, alt: '' }) : null, p.name)));
      } catch (e) { /* ignore */ }
    };
    input.addEventListener('input', () => { clearTimeout(input._t); input._t = setTimeout(refresh, 200); });
    let saved = false;
    const m = modal({
      title,
      body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
        faceId ? h('img', { src: faceUrl(faceId), alt: '', style: { width: '96px', height: '96px', borderRadius: '50%', objectFit: 'cover', alignSelf: 'center' } }) : null,
        input, list,
        h('span', { class: 'hint' }, 'Pick an existing name to add this face to that person.')),
      onClose: () => { if (!saved && onCancel) onCancel(); },
      actions: [
        faceId ? { label: 'Not a face', cls: 'ghost', run: async () => { saved = true; await api(`/api/faces/${faceId}`, { method: 'PATCH', body: { reject: true } }); await this.refreshDetail(); } } : null,
        { label: 'Cancel', cls: 'ghost' },
        { label: 'Save', cls: 'primary', run: async () => {
          const name = input.value.trim();
          if (!name && !chosen) { toast('Type a name.', true); return true; }
          saved = true;
          await onSave(name, chosen);
        } },
      ].filter(Boolean),
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') m.box.querySelector('.mfoot .btn.primary').click(); });
  }
}

export function pickOnMap(lat, lon) {
  return new Promise((resolve) => {
    let chosen = null, done = false;
    const box = h('div', { style: { height: '360px', borderRadius: '12px', overflow: 'hidden' } });
    const label = h('div', { class: 'hint' }, 'Click the map where this photo was taken.');
    modal({
      title: 'Pick the location', wide: true,
      body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } }, box, label),
      onClose: () => { if (!done) resolve(null); },
      actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Use this spot', cls: 'primary', run: () => {
        if (!chosen) { toast('Click the map first.', true); return true; }
        done = true; resolve(chosen);
      } }],
    });
    loadLeaflet().then(L => {
      const m = L.map(box).setView(lat ? [lat, lon] : [39.8, -86.15], lat ? 13 : 5);
      L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTR }).addTo(m);
      let mk = lat ? L.marker([lat, lon]).addTo(m) : null;
      m.on('click', (e) => {
        chosen = { lat: +e.latlng.lat.toFixed(6), lon: +e.latlng.lng.toFixed(6) };
        if (mk) mk.setLatLng(e.latlng); else mk = L.marker(e.latlng).addTo(m);
        label.textContent = `${chosen.lat}, ${chosen.lon}`;
      });
      setTimeout(() => m.invalidateSize(), 100);
    });
  });
}

void MONTHS_LONG;
