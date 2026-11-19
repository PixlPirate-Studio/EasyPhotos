// The photo grid used by the timeline, people, places, albums, tags and search.
// Justified rows grouped by day, endless scrolling, select-many with actions.
import { h, icon, api, thumbUrl, fmtDay, fmtDuration, toast, errToast, modal, confirmDialog, download, MONTHS, MONTHS_LONG, num } from './ui.js';
import { openViewer } from './viewer.js';
import { state } from './state.js';

export class PhotoGrid {
  constructor({ query = {}, scrubber = false, header = null, emptyText = 'No photos here yet.', hiddenView = false } = {}) {
    this.query = { ...query };
    this.useScrubber = scrubber;
    this.header = header;
    this.emptyText = emptyText;
    this.hiddenView = hiddenView;
    this.items = [];
    this.next = null;
    this.loading = false;
    this.done = false;
    this.selected = new Set();
    this.lastClicked = null;
    this.jumped = false;
    this.el = h('div', { class: 'timeline' });
    this.groupsEl = h('div', { class: 'groups' });
    this.sentinel = h('div', { class: 'load-more' });
    this.el.append(this.groupsEl);
    if (this.useScrubber) { this.scrubEl = h('nav', { class: 'scrubber', 'aria-label': 'Jump to date' }); this.el.append(this.scrubEl); }
    this.selbar = null;
    this._resize = () => { clearTimeout(this._rt); this._rt = setTimeout(() => this.render(), 120); };
  }

  mount(parent) {
    parent.append(this.el);
    this.io = new IntersectionObserver((ents) => { if (ents.some(e => e.isIntersecting)) this.loadMore(); }, { rootMargin: '1200px' });
    window.addEventListener('resize', this._resize);
    this.reload();
    if (this.useScrubber) this.loadMonths();
    return this;
  }

  destroy() {
    this.io && this.io.disconnect();
    window.removeEventListener('resize', this._resize);
    this.clearSelection();
  }

  async reload(before = null) {
    this.items = []; this.next = before; this.done = false; this.jumped = !!before;
    this.groupsEl.innerHTML = '';
    this.groupsEl.append(h('div', { class: 'spinner' }));
    await this.loadMore(true);
  }

  async loadMore(first = false) {
    if (this.loading || (this.done && !first)) return;
    this.loading = true;
    try {
      const qs = new URLSearchParams({ ...this.query, limit: 400 });
      if (this.next) { qs.set('before_ts', this.next.before_ts); qs.set('before_id', this.next.before_id); }
      const res = await api('/api/assets?' + qs);
      this.items.push(...res.items);
      this.next = res.next;
      this.done = !res.next;
      this.render();
    } catch (e) { errToast(e); }
    finally { this.loading = false; }
  }

  async loadMonths() {
    try {
      const months = await api('/api/assets/months?' + new URLSearchParams(this.query));
      this.scrubEl.innerHTML = '';
      let year = null;
      for (const m of months) {
        const [y, mo] = m.month.split('-');
        if (y !== year) {
          year = y;
          this.scrubEl.append(h('button', { class: 'yr', onclick: () => this.jumpTo(+y, 12) }, y));
        }
        this.scrubEl.append(h('button', { title: `${MONTHS_LONG[+mo - 1]} ${y} · ${num(m.count)}`, onclick: () => this.jumpTo(+y, +mo) }, MONTHS[+mo - 1]));
      }
    } catch (e) { /* not important */ }
  }

  jumpTo(year, month) {
    // Show photos from the end of that month going back.
    const ts = Date.UTC(month === 12 ? year + 1 : year, month === 12 ? 0 : month, 1) / 1000;
    this.reload({ before_ts: ts, before_id: 2 ** 52 });
    window.scrollTo({ top: 0 });
  }

  rowHeight() {
    const w = this.groupsEl.clientWidth || window.innerWidth;
    if (w < 500) return 118;
    if (w < 900) return 160;
    return 210;
  }

  render() {
    const width = Math.floor(this.groupsEl.clientWidth || this.el.clientWidth || window.innerWidth - 40);
    this.groupsEl.innerHTML = '';
    if (this.jumped) {
      this.groupsEl.append(h('div', { class: 'row', style: { marginBottom: '14px' } },
        h('button', { class: 'btn small', onclick: () => { this.reload(); } }, '↑ Back to newest')));
    }
    if (!this.items.length) {
      if (this.done) this.groupsEl.append(h('div', { class: 'empty' }, icon('photos'), h('div', {}, this.emptyText)));
      else this.groupsEl.append(h('div', { class: 'spinner' }));
      return;
    }
    const days = [];
    let cur = null;
    for (const it of this.items) {
      const d = (it.t || '').slice(0, 10);
      if (!cur || cur.day !== d) { cur = { day: d, items: [] }; days.push(cur); }
      cur.items.push(it);
    }
    const target = this.rowHeight();
    const gap = 4;
    let index = 0;
    for (const day of days) {
      const cities = {};
      day.items.forEach(i => { if (i.c) cities[i.c] = (cities[i.c] || 0) + 1; });
      const place = Object.entries(cities).sort((a, b) => b[1] - a[1]).map(x => x[0]).slice(0, 2).join(' · ');
      const dayIds = day.items.map(i => i.id);
      const sec = h('section', { class: 'day' },
        h('div', { class: 'day-head' },
          h('h3', {}, fmtDay(day.day)),
          place ? h('span', { class: 'place' }, place) : null,
          h('button', { class: 'btn small ghost selday', onclick: () => this.toggleMany(dayIds) }, 'Select day')));
      let row = [], sum = 0;
      const flush = (last) => {
        if (!row.length) return;
        const avail = width - gap * (row.length - 1);
        let hgt = avail / sum;
        if (last && hgt > target * 1.25) hgt = target;
        const r = h('div', { class: 'jrow', style: { height: `${Math.floor(hgt)}px` } });
        for (const it of row) r.append(this.tile(it, Math.floor(hgt * (it.w / it.h))));
        sec.append(r);
        row = []; sum = 0;
      };
      for (const it of day.items) {
        it._i = index++;
        const ar = Math.max(0.3, Math.min(4, (it.w || 1) / (it.h || 1)));
        row.push(it); sum += ar;
        if (sum * target + gap * (row.length - 1) >= width) flush(false);
      }
      flush(true);
      this.groupsEl.append(sec);
    }
    this.groupsEl.append(this.sentinel);
    this.io.observe(this.sentinel);
    this.el.classList.toggle('selecting', this.selected.size > 0);
  }

  tile(it, w) {
    const t = h('div', { class: 'tile' + (this.selected.has(it.id) ? ' sel' : ''), style: { width: `${w}px` }, 'data-id': it.id });
    const im = h('img', { loading: 'lazy', decoding: 'async', alt: '' });
    im.addEventListener('load', () => im.classList.add('ok'));
    im.src = thumbUrl(it.id, it.v);
    const selBtn = h('button', { class: 'tsel', 'aria-label': 'Select' });
    selBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></svg>';
    selBtn.addEventListener('click', (e) => { e.stopPropagation(); this.toggle(it, e.shiftKey); });
    t.append(im, selBtn);
    if (it.k === 'v') t.append(h('span', { class: 'vbadge' }, icon('play', 'small'), fmtDuration(it.d)));
    else if (it.l) t.append(h('span', { class: 'live' }, 'LIVE'));
    if (it.f) { const s = icon('heart', 'fav'); t.append(s); }
    // Long-press to start selecting on phones
    let pressTimer = null, pressed = false;
    t.addEventListener('touchstart', () => { pressed = false; pressTimer = setTimeout(() => { pressed = true; this.toggle(it); if (navigator.vibrate) navigator.vibrate(20); }, 450); }, { passive: true });
    t.addEventListener('touchend', () => clearTimeout(pressTimer));
    t.addEventListener('touchmove', () => clearTimeout(pressTimer), { passive: true });
    t.addEventListener('click', (e) => {
      if (pressed) { pressed = false; return; }
      if (this.selected.size || e.shiftKey || e.ctrlKey || e.metaKey) { this.toggle(it, e.shiftKey); return; }
      openViewer({ items: this.items, index: it._i, grid: this });
    });
    t.querySelector('svg.i')?.setAttribute('width', 14);
    return t;
  }

  toggle(it, range = false) {
    if (range && this.lastClicked !== null) {
      const a = Math.min(this.lastClicked, it._i), b = Math.max(this.lastClicked, it._i);
      for (let i = a; i <= b; i++) this.selected.add(this.items[i].id);
    } else if (this.selected.has(it.id)) this.selected.delete(it.id);
    else this.selected.add(it.id);
    this.lastClicked = it._i;
    this.updateSelection();
  }

  toggleMany(ids) {
    const all = ids.every(i => this.selected.has(i));
    ids.forEach(i => all ? this.selected.delete(i) : this.selected.add(i));
    this.updateSelection();
  }

  clearSelection() {
    this.selected.clear();
    this.updateSelection();
  }

  updateSelection() {
    this.groupsEl.querySelectorAll('.tile').forEach(t => t.classList.toggle('sel', this.selected.has(+t.dataset.id)));
    this.el.classList.toggle('selecting', this.selected.size > 0);
    if (this.selected.size) this.showSelbar(); else if (this.selbar) { this.selbar.remove(); this.selbar = null; }
  }

  showSelbar() {
    const u = state.user;
    if (!this.selbar) {
      this.selbar = h('div', { class: 'selbar' });
      document.querySelector('.shell')?.prepend(this.selbar);
    }
    const ids = () => [...this.selected];
    const act = (ic, label, fn, cls = '') => h('button', { class: 'act ' + cls, title: label, onclick: fn }, icon(ic), h('span', {}, label));
    this.selbar.innerHTML = '';
    this.selbar.append(
      h('button', { class: 'iconbtn', 'aria-label': 'Clear selection', onclick: () => this.clearSelection() }, icon('x')),
      h('span', { class: 'count' }, `${this.selected.size} selected`),
      u.can_download ? act('down', 'Download (.zip)', () => zipDownload(ids())) : null,
      u.is_admin ? act('albumadd', 'Add to album', () => albumDialog(ids(), () => this.clearSelection())) : null,
      u.is_admin ? act('tag', 'Add tag', () => tagDialog(ids(), () => { this.clearSelection(); })) : null,
      u.is_admin ? act('calendar', 'Fix dates', () => fixDatesDialog(ids(), () => { this.clearSelection(); this.reload(); })) : null,
      u.is_admin ? act(this.hiddenView ? 'eye' : 'eyeoff', this.hiddenView ? 'Unhide' : 'Hide', async () => {
        try {
          await api('/api/assets/bulk', { method: 'POST', body: { ids: ids(), action: this.hiddenView ? 'unhide' : 'hide' } });
          toast(this.hiddenView ? 'Photos are visible again.' : 'Hidden. Find them under Library › Hidden.');
          this.clearSelection(); this.reload();
        } catch (e) { errToast(e); }
      }) : null,
      u.is_admin && state.allowDelete ? act('trash', 'Delete', () => deleteDialog(ids(), () => { this.clearSelection(); this.reload(); }), 'red') : null,
      h('button', { class: 'act', style: { marginLeft: 'auto' }, onclick: async () => {
        try { const all = await api('/api/assets/ids?' + new URLSearchParams(this.query)); all.forEach(i => this.selected.add(i)); this.updateSelection(); }
        catch (e) { errToast(e); }
      } }, h('span', {}, 'Select all')),
    );
  }

  removeIds(ids) {
    const s = new Set(ids);
    this.items = this.items.filter(i => !s.has(i.id));
    this.render();
  }
}

// ---------------------------------------------------------------- selection actions

export async function zipDownload(ids) {
  try {
    const res = await api('/api/download/prepare', { method: 'POST', body: { ids } });
    toast(`Preparing ${res.count} file${res.count === 1 ? '' : 's'}…`);
    download(res.url);
  } catch (e) { errToast(e); }
}

export async function albumDialog(ids, after) {
  let albums = [];
  try { albums = await api('/api/albums'); } catch (e) { /* ignore */ }
  const sel = h('select', { class: 'input' },
    h('option', { value: '' }, '+ New album…'),
    albums.map(a => h('option', { value: a.id }, `${a.name} (${a.n})`)));
  const name = h('input', { class: 'input', placeholder: 'Album name' });
  const nameField = h('label', { class: 'field' }, 'New album name', name);
  sel.addEventListener('change', () => nameField.classList.toggle('hidden', !!sel.value));
  modal({
    title: `Add ${ids.length} to an album`,
    body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } }, h('label', { class: 'field' }, 'Album', sel), nameField),
    actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Add', cls: 'primary', run: async () => {
      const body = { ids, action: 'add_album' };
      if (sel.value) body.album_id = +sel.value; else body.album_name = name.value;
      await api('/api/assets/bulk', { method: 'POST', body });
      toast('Added to album.');
      after && after();
    } }],
  });
}

export function tagDialog(ids, after) {
  const input = h('input', { class: 'input', placeholder: 'e.g. Dog, Honda, Camping', list: 'taglist' });
  const dl = h('datalist', { id: 'taglist' });
  api('/api/tags').then(tags => tags.forEach(t => dl.append(h('option', { value: t.name })))).catch(() => {});
  modal({
    title: `Tag ${ids.length} photo${ids.length === 1 ? '' : 's'}`,
    body: h('div', {}, h('label', { class: 'field' }, 'Tag', input), dl),
    actions: [
      { label: 'Remove tag', cls: 'ghost', run: async () => {
        if (!input.value.trim()) { toast('Type the tag to remove.', true); return true; }
        await api('/api/assets/bulk', { method: 'POST', body: { ids, action: 'remove_tag', tag: input.value } });
        toast('Tag removed.'); after && after();
      } },
      { label: 'Add tag', cls: 'primary', run: async () => {
        if (!input.value.trim()) { toast('Type a tag.', true); return true; }
        await api('/api/assets/bulk', { method: 'POST', body: { ids, action: 'add_tag', tag: input.value } });
        toast(`Tagged “${input.value.trim()}”.`); after && after();
      } },
    ],
  });
}

export function fixDatesDialog(ids, after) {
  const now = new Date();
  const year = h('input', { class: 'input', type: 'number', min: 1850, max: 2100, value: now.getFullYear(), style: { width: '110px' }, 'aria-label': 'Year' });
  const month = h('select', { class: 'input', 'aria-label': 'Month' }, h('option', { value: '' }, 'Month unknown'), MONTHS_LONG.map((m, i) => h('option', { value: i + 1 }, m)));
  const day = h('input', { class: 'input', type: 'number', min: 1, max: 31, placeholder: 'Day', style: { width: '90px' }, 'aria-label': 'Day' });
  const amount = h('input', { class: 'input', type: 'number', value: 1, style: { width: '90px' }, 'aria-label': 'Amount' });
  const unit = h('select', { class: 'input', 'aria-label': 'Unit' }, ['minutes', 'hours', 'days', 'years'].map(u => h('option', { value: u, selected: u === 'hours' }, u)));
  const dir = h('select', { class: 'input', 'aria-label': 'Direction' }, h('option', { value: '1' }, 'later'), h('option', { value: '-1' }, 'earlier'));
  const setR = h('input', { type: 'radio', name: 'fix', checked: true });
  const shiftR = h('input', { type: 'radio', name: 'fix' });
  const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } },
    h('label', { class: 'opt' }, setR, h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px', flex: 1 } },
      h('b', {}, 'Set them all to one date'), h('span', { class: 'hint' }, 'Good for old scans. Their order is kept.'),
      h('div', { class: 'row' }, month, day, year))),
    h('label', { class: 'opt' }, shiftR, h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px', flex: 1 } },
      h('b', {}, 'Shift them by a set amount'), h('span', { class: 'hint' }, 'Good when a camera’s clock was wrong.'),
      h('div', { class: 'row' }, amount, unit, dir))),
    h('span', { class: 'hint' }, 'Saved in EasyPhotos. Your original files aren’t changed.'));
  modal({
    title: `Fix dates for ${ids.length} photo${ids.length === 1 ? '' : 's'}`, body,
    actions: [{ label: 'Cancel', cls: 'ghost' }, { label: `Apply to ${ids.length}`, cls: 'primary', run: async () => {
      let req;
      if (setR.checked) req = { ids, action: 'fix_dates', mode: 'set', year: +year.value, month: month.value || null, day: day.value || null };
      else {
        const mult = { minutes: 60, hours: 3600, days: 86400, years: 365.25 * 86400 }[unit.value];
        req = { ids, action: 'fix_dates', mode: 'shift', seconds: Math.round(+amount.value * mult * +dir.value) };
      }
      await api('/api/assets/bulk', { method: 'POST', body: req });
      toast('Dates fixed.');
      after && after();
    } }],
  });
}

export async function deleteDialog(ids, after) {
  const ok = await confirmDialog({
    title: `Delete ${ids.length} photo${ids.length === 1 ? '' : 's'}?`,
    text: 'They’ll go to the Recycle Bin. You can restore them from there until you empty it.',
    confirmLabel: 'Send to Recycle Bin', danger: true, typeWord: ids.length > 1 ? 'DELETE' : null,
  });
  if (!ok) return;
  try {
    const res = await api('/api/assets/bulk', { method: 'POST', body: { ids, action: 'delete' } });
    if (res.errors && res.errors.length) toast(res.errors[0], true);
    if (res.moved) toast(`${res.moved} moved to the Recycle Bin.`);
    after && after();
  } catch (e) { errToast(e); }
}
