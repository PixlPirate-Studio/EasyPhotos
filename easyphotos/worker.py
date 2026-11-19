"""Background worker: scanning, thumbnails, faces, duplicates.

Runs as its own low-priority process so the web pages stay fast and your
other apps (Jellyfin, etc.) always get the CPU first. It never stops on its
own; it just uses what is left over.
"""
import datetime as dt
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import cluster, config, db, dupes, media, recycle, scanner, settings, xmp

log = logging.getLogger("easyphotos.worker")


def set_status(name: str, state: str, message: str = "", rate=None) -> None:
    db.ex("INSERT INTO job_status(name, state, message, rate, updated_at) VALUES (?,?,?,?,?) "
          "ON CONFLICT(name) DO UPDATE SET state=excluded.state, message=excluded.message, "
          "rate=COALESCE(excluded.rate, job_status.rate), updated_at=excluded.updated_at",
          (name, state, message, rate, time.time()))


_act_count = 0
_log_files = True
KIND_LABEL = {"scan": "SCAN", "thumbs": "THUMB", "faces": "FACES", "duplicates": "DUPES", "command": "ADMIN"}


def activity(entries) -> None:
    """Adds lines to the live activity feed (and the Unraid container log). entries: list of (kind, level, text)."""
    global _act_count
    if not entries:
        return
    for k, lv, t in entries:
        if lv in ("warning", "error"):
            (log.error if lv == "error" else log.warning)("%-5s %s", KIND_LABEL.get(k, k.upper()), t)
        elif _log_files or k in ("command", "duplicates") or t.startswith(("Finished", "Started", "Matched")):
            log.info("%-5s %s", KIND_LABEL.get(k, k.upper()), t)
    now = time.time()
    db.exmany("INSERT INTO activity(ts, kind, level, text) VALUES (?,?,?,?)", [(now, k, lv, t[:500]) for k, lv, t in entries])
    _act_count += len(entries)
    if _act_count > 200:
        _act_count = 0
        db.ex("DELETE FROM activity WHERE id <= (SELECT MAX(id) - 2000 FROM activity)")


def rel_label(row) -> str:
    return row["path"] if row else "?"


def current_threads(s: dict) -> int:
    threads = int(s["worker_threads"])
    if s["night_boost"]:
        h = dt.datetime.now().hour
        start, end = int(s["night_start"]), int(s["night_end"])
        in_window = (start <= h < end) if start <= end else (h >= start or h < end)
        if in_window:
            threads = max(threads, int(s["night_threads"]))
    return max(1, min(threads, os.cpu_count() or threads))


class Worker:
    def __init__(self):
        self.force_scan = set()
        self.dupes_dirty = True
        self.last_dupes = 0.0
        self.last_daily = 0.0
        self.face_times = []
        self.meta_times = []
        self.scan_thread = None
        self.scan_stop = threading.Event()
        self.last_beat = 0.0
        self.threads = 1
        self.last_summary = 0.0
        self.was_busy = None

    def heartbeat(self, force=False):
        if force or time.time() - self.last_beat > 20:
            self.last_beat = time.time()
            set_status("worker", "running", f"Using {self.threads} thread{'s' if self.threads != 1 else ''} at low priority")
        return False

    # ------------------------------------------------------------ commands
    def handle_commands(self) -> bool:
        rows = db.q("SELECT id, name, args FROM job_commands ORDER BY id")
        if not rows:
            return False
        db.ex("DELETE FROM job_commands WHERE id <= ?", (rows[-1]["id"],))
        for r in rows:
            name = r["name"]
            args = json.loads(r["args"] or "{}")
            log.debug("Command: %s %s", name, args)
            friendly = {"scan": "Scan for new photos requested", "find_faces": "Find faces started", "rescan_faces": "Re-scan of all faces started",
                        "recluster": "Re-grouping faces", "rebuild_thumbs": "Rebuilding all thumbnails", "duplicates": "Duplicate check requested",
                        "refresh_places": "Updating place names"}.get(name, name)
            activity([("command", "info", friendly)])
            if name == "stop_scan":
                self.scan_stop.set()
                self.force_scan.clear()
            elif name == "scan":
                lib = args.get("library_id")
                if lib:
                    self.force_scan.add(int(lib))
                else:
                    self.force_scan.update(x["id"] for x in db.q("SELECT id FROM libraries WHERE enabled=1"))
            elif name == "find_faces":
                settings.set_many({"faces_started": True, "faces_enabled": True})
                db.ex("UPDATE assets SET face_status=0 WHERE face_status=-1")
            elif name == "rescan_faces":
                # Throw away automatic faces in groups without a name and look again.
                db.ex("DELETE FROM faces WHERE manual=0 AND confirmed=0 AND (person_id IS NULL OR person_id IN "
                      "(SELECT id FROM persons WHERE name IS NULL))")
                db.ex("DELETE FROM persons WHERE name IS NULL")
                db.ex("UPDATE assets SET face_status=0")
                settings.set_many({"faces_started": True})
            elif name == "recluster":
                set_status("faces", "running", "Re-grouping faces…")
                n = cluster.recluster_unnamed()
                set_status("faces", "idle", f"Re-grouped {n} faces")
            elif name == "rebuild_thumbs":
                db.ex("UPDATE assets SET meta_status=0, thumb_ver=thumb_ver+1 WHERE deleted_at IS NULL")
            elif name == "duplicates":
                self.dupes_dirty = True
                self.last_dupes = 0
            elif name == "refresh_places":
                scanner.refresh_places()
        return True

    # ------------------------------------------------------------ scanning
    def maybe_scan(self, s) -> bool:
        """Starts a scan in its own thread so thumbnails can be made at the same time."""
        if self.scan_thread is not None and self.scan_thread.is_alive():
            return False
        interval = float(s["scan_interval_hours"]) * 3600
        todo = []
        for lib in db.q("SELECT * FROM libraries WHERE enabled=1"):
            if lib["scan_stopped"] and lib["id"] not in self.force_scan:
                continue  # stopped by the admin; only "Resume scan" starts it again
            due = lib["last_scan_at"] is None or (interval > 0 and time.time() - lib["last_scan_at"] > interval)
            if lib["id"] in self.force_scan or due:
                self.force_scan.discard(lib["id"])
                todo.append(dict(lib))
        if not todo:
            return False
        self.scan_stop.clear()
        self.scan_thread = threading.Thread(target=self._scan_libraries, args=(todo,), daemon=True)
        self.scan_thread.start()
        return True

    def _scan_libraries(self, libs):
        for lib in libs:
            if self.scan_stop.is_set():
                break
            try:
                self._scan_one(lib)
            except Exception as e:
                log.exception("Scan of %s failed: %s", lib["name"], e)
                activity([("scan", "error", f"Scan of {lib['name']} failed: {e}")])
                set_status("scan", "error", f"Scan of {lib['name']} failed: {e}")

    def _scan_one(self, lib):
        name = lib["name"]
        root = lib["path"].rstrip("/")
        set_status("scan", "running", f"Step 1: finding photos in {name}…")
        activity([("scan", "info", f"Started looking for photos in {name} ({lib['path']})")])
        last = [0.0]

        def on_folder(d):
            if time.time() - last[0] > 1.0:
                last[0] = time.time()
                rel = str(d)[len(root):].lstrip("/") or "(top folder)"
                activity([("scan", "info", f"Looking in {name}/{rel}")])

        def progress(st):
            set_status("scan", "running", f"Step 1: finding photos in {name}… {st['seen']:,} found in {st['folders']:,} folders so far")

        stats = scanner.scan_library(lib, progress=progress, on_folder=on_folder, should_stop=self.scan_stop.is_set)
        if stats["stopped"]:
            msg = f"{name}: scan stopped after finding {stats['seen']:,} files. Thumbnails continue for those."
            set_status("scan", "stopped", msg)
            activity([("scan", "info", msg)])
            return
        if stats["missing"]:
            msg = f"{name}: folder {lib['path']} was not found inside the container. Check the Path mapping in Unraid."
            set_status("scan", "error", msg)
            activity([("scan", "error", msg)])
            return
        msg = (f"{name}: found {stats['seen']:,} photos/videos in {stats['folders']:,} folders "
               f"({stats['added']:,} new, {stats['changed']:,} changed, {stats['removed']:,} removed)")
        level = "info"
        if stats["unreadable"]:
            msg += f". {stats['unreadable']:,} files/folders couldn't be read (permissions?). See Logs."
            level = "warning"
        elif stats["seen"] == 0:
            msg += ". The folder looks empty. Is the Host Path in Unraid right?"
            level = "warning"
        set_status("scan", "error" if level != "info" else "idle", msg)
        activity([("scan", level, "Finished: " + msg)])
        if stats["added"] or stats["changed"] or stats["removed"]:
            self.dupes_dirty = True

    # ------------------------------------------------------------ metadata + thumbnails
    def meta_batch(self, threads: int) -> bool:
        ids = [r["id"] for r in db.q(
            "SELECT id FROM assets WHERE meta_status=0 AND deleted_at IS NULL ORDER BY taken_ts DESC NULLS FIRST, id DESC LIMIT ?",
            (threads * 8,))]
        if not ids:
            return False
        left = db.val("SELECT COUNT(*) FROM assets WHERE meta_status=0 AND deleted_at IS NULL", default=0)
        paths = {r["id"]: r for r in db.q(
            f"SELECT a.id, l.name || '/' || a.path AS path FROM assets a JOIN libraries l ON l.id=a.library_id "
            f"WHERE a.id IN ({','.join('?' * len(ids))})", ids)}
        set_status("thumbs", "running", f"Making thumbnails… now: {rel_label(paths.get(ids[0]))}")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=threads) as pool:
            results = list(pool.map(scanner.process_meta, ids))
        entries = []
        for aid, cols in zip(ids, results):
            scanner.save_meta(aid, cols)
            label = rel_label(paths.get(aid))
            if cols.get("meta_status") == -1:
                entries.append(("thumbs", "warning", f"Couldn't read {label}: {cols.get('error')}"))
            else:
                entries.append(("thumbs", "info", f"Thumbnail made: {label}"))
        activity(entries)
        n = scanner.pair_live_photos([aid for aid, cols in zip(ids, results) if cols.get("meta_status") == 1])
        if n:
            activity([("thumbs", "info", f"Matched {n} Live Photo{'s' if n > 1 else ''} with their photo")])
        self.meta_times.append((len(ids), time.time() - t0))
        self.meta_times = self.meta_times[-30:]
        rate = sum(x[0] for x in self.meta_times) / (sum(x[1] for x in self.meta_times) or 1) * 60
        set_status("thumbs", "running", f"Making thumbnails… last: {rel_label(paths.get(ids[-1]))}", rate=rate)
        self.dupes_dirty = True
        if left <= len(ids):
            set_status("thumbs", "idle", "All thumbnails made")
        return True

    # ------------------------------------------------------------ faces
    def faces_batch(self, s, threads: int) -> bool:
        if not (s["faces_enabled"] and s["faces_started"]):
            return False
        # Videos and Live Photo halves are skipped for faces.
        db.ex("UPDATE assets SET face_status=2 WHERE face_status=0 AND (kind='video' OR is_live_part=1) AND meta_status=1")
        rows = db.q("SELECT id, width, height FROM assets WHERE face_status=0 AND meta_status=1 AND deleted_at IS NULL "
                    "ORDER BY taken_ts DESC LIMIT 16")
        if not rows:
            pending_meta = db.val("SELECT COUNT(*) FROM assets WHERE meta_status=0 AND deleted_at IS NULL", default=0)
            if not pending_meta:
                set_status("faces", "idle", "Done")
            return False
        try:
            from .faces import get_engine, to_blob
            engine = get_engine(threads)
        except Exception as e:
            log.error("Face AI could not start: %s", e)
            set_status("faces", "error", f"Face AI could not start: {e}")
            time.sleep(30)
            return False
        cl = cluster.Clusterer()
        min_score = float(s["face_min_score"])
        min_size = int(s["face_min_size"])
        t0 = time.time()
        paths = {x["id"]: x for x in db.q(
            f"SELECT a.id, l.name || '/' || a.path AS path FROM assets a JOIN libraries l ON l.id=a.library_id "
            f"WHERE a.id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])}
        entries = []
        for r in rows:
            aid = r["id"]
            set_status("faces", "running", f"Finding faces… now: {rel_label(paths.get(aid))}")
            try:
                p = media.thumb_path(aid, "p")
                with Image.open(p) as im:
                    rgb = np.asarray(im.convert("RGB"))
                ih, iw = rgb.shape[:2]
                found = engine.detect(rgb, min_score=min_score, min_size=min_size)
                for box, score, kps in found:
                    emb = engine.embed(rgb, kps)
                    x1, y1, x2, y2 = (float(v) for v in box)
                    cur = db.ex(
                        "INSERT INTO faces(asset_id, x1, y1, x2, y2, score, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (aid, x1 / iw, y1 / ih, x2 / iw, y2 / ih, score, to_blob(emb), time.time()))
                    fid = cur.lastrowid
                    save_face_crop(fid, rgb, (x1, y1, x2, y2))
                    cl.assign(fid, emb)
                db.ex("UPDATE assets SET face_status=1 WHERE id=?", (aid,))
                n = len(found)
                entries.append(("faces", "info", f"{'No faces' if not n else f'{n} face' + ('s' if n > 1 else '')} in {rel_label(paths.get(aid))}"))
            except Exception as e:
                log.warning("Face scan failed for asset %s: %s", aid, e)
                db.ex("UPDATE assets SET face_status=-1, error=? WHERE id=?", (str(e)[:300], aid))
                entries.append(("faces", "warning", f"Face finding failed for {rel_label(paths.get(aid))}: {e}"))
        activity(entries)
        cl.finish()
        dt_ = time.time() - t0
        self.face_times.append((len(rows), dt_))
        self.face_times = self.face_times[-30:]
        n = sum(x[0] for x in self.face_times)
        secs = sum(x[1] for x in self.face_times) or 1
        rate = n / secs * 60  # photos per minute
        set_status("faces", "running", f"Finding faces… last: {rel_label(paths.get(rows[-1]['id']))}", rate=rate)
        return True

    def embed_manual(self, s, threads: int) -> bool:
        """Works out fingerprints for faces the admin drew by hand."""
        if not s["faces_enabled"]:
            return False
        rows = db.q("SELECT id, asset_id, person_id, x1, y1, x2, y2 FROM faces WHERE manual=1 AND embedding IS NULL "
                    "AND rejected=0 LIMIT 20")
        if not rows:
            return False
        try:
            from .faces import get_engine, to_blob
            engine = get_engine(threads)
        except Exception as e:
            log.error("Face AI could not start: %s", e)
            db.ex(f"UPDATE faces SET embedding=x'' WHERE id IN ({','.join(str(r['id']) for r in rows)})")
            return False
        touched = set()
        for r in rows:
            try:
                with Image.open(media.thumb_path(r["asset_id"], "p")) as im:
                    rgb = np.asarray(im.convert("RGB"))
                ih, iw = rgb.shape[:2]
                emb, _ = engine.embed_box(rgb, (r["x1"] * iw, r["y1"] * ih, r["x2"] * iw, r["y2"] * ih))
                db.ex("UPDATE faces SET embedding=? WHERE id=?", (to_blob(emb), r["id"]))
                if r["person_id"]:
                    touched.add(r["person_id"])
            except Exception as e:
                log.warning("Could not read drawn face %s: %s", r["id"], e)
                db.ex("UPDATE faces SET embedding=x'' WHERE id=?", (r["id"],))
        for pid in touched:
            cluster.recompute_person(pid)
        return True

    # ------------------------------------------------------------ progress summary for the container log
    def summary(self, s) -> None:
        if time.time() - self.last_summary < 60:
            return
        self.last_summary = time.time()
        c = db.q1("SELECT COUNT(*) AS total, SUM(CASE WHEN meta_status=0 THEN 1 ELSE 0 END) AS thumbs_left, "
                  "SUM(CASE WHEN meta_status=-1 THEN 1 ELSE 0 END) AS errors, "
                  "SUM(CASE WHEN kind='image' AND is_live_part=0 THEN 1 ELSE 0 END) AS images, "
                  "SUM(CASE WHEN face_status=0 THEN 1 ELSE 0 END) AS faces_left FROM assets WHERE deleted_at IS NULL")
        total, tl, fl = c["total"] or 0, c["thumbs_left"] or 0, c["faces_left"] or 0
        scanning = self.scan_thread is not None and self.scan_thread.is_alive()
        faces_on = s["faces_enabled"] and s["faces_started"]
        busy = scanning or tl or (faces_on and fl)
        if not busy:
            if self.was_busy is not False:
                log.info("STATUS All caught up: %s photos & videos, all thumbnails made%s. Waiting for new photos.",
                         f"{total:,}", ", faces done" if faces_on else "")
            self.was_busy = False
            return
        self.was_busy = True
        parts = []
        if scanning:
            found = db.val("SELECT SUM(scan_found) FROM libraries WHERE scan_started_at IS NOT NULL", default=0) or 0
            parts.append(f"scan: {found:,} found so far")
        rate = sum(x[0] for x in self.meta_times) / (sum(x[1] for x in self.meta_times) or 1) * 60 if self.meta_times else 0
        eta = f", ~{_eta(tl / rate * 60)} left" if rate and tl else ""
        parts.append(f"thumbnails: {total - tl:,}/{total:,} done ({rate:,.0f}/min{eta})" if tl else f"thumbnails: all {total:,} done")
        if faces_on:
            images = c["images"] or 0
            frate = sum(x[0] for x in self.face_times) / (sum(x[1] for x in self.face_times) or 1) * 60 if self.face_times else 0
            feta = f", ~{_eta(fl / frate * 60)} left" if frate and fl else ""
            parts.append(f"faces: {max(0, images - fl):,}/{images:,} photos checked{feta}")
        if c["errors"]:
            parts.append(f"{c['errors']:,} files unreadable")
        log.info("STATUS %s · %d threads", " · ".join(parts), self.threads)

    # ------------------------------------------------------------ others
    def maybe_dupes(self, s) -> bool:
        if not s["duplicates_enabled"] or not self.dupes_dirty:
            return False
        if time.time() - self.last_dupes < 600:
            return False
        if db.val("SELECT COUNT(*) FROM assets WHERE meta_status=0 AND deleted_at IS NULL", default=0) > 0:
            return False
        set_status("duplicates", "running", "Checking for duplicates…")
        activity([("duplicates", "info", "Checking for duplicate photos…")])
        res = dupes.rebuild(should_stop=self.heartbeat)
        self.dupes_dirty = False
        self.last_dupes = time.time()
        set_status("duplicates", "idle", f"{res.get('exact', 0)} exact, {res.get('similar', 0)} look-alike groups")
        activity([("duplicates", "info", f"Duplicate check done: {res.get('exact', 0)} groups of exact copies, {res.get('similar', 0)} look-alike groups")])
        return True

    def maybe_xmp(self, s) -> bool:
        if not s["xmp_enabled"]:
            return False
        rows = db.q("SELECT id FROM assets WHERE xmp_dirty=1 LIMIT 200")
        for r in rows:
            xmp.write_sidecar(r["id"])
            db.ex("UPDATE assets SET xmp_dirty=0 WHERE id=?", (r["id"],))
        return bool(rows)

    def daily(self, s) -> None:
        if time.time() - self.last_daily < 3600:
            return
        self.last_daily = time.time()
        n = recycle.auto_empty(int(s["recycle_auto_days"]))
        if n:
            log.info("Emptied %d old items from the Recycle Bin", n)
        db.ex("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    # ------------------------------------------------------------ loop
    def run(self):
        set_status("worker", "running", "Started")
        try:
            n = scanner.pair_live_photos()
            if n:
                log.info("Matched %d Live Photos with their photos", n)
                activity([("thumbs", "info", f"Matched {n:,} Live Photos with their photos")])
        except Exception as e:
            log.warning("Live Photo matching failed: %s", e)
        while True:
            try:
                did = self.handle_commands()
                s = settings.get_all()
                if s["paused"]:
                    set_status("worker", "paused", "Paused by admin")
                    self.last_beat = 0
                    time.sleep(5)
                    continue
                threads = current_threads(s)
                if threads != self.threads:
                    self.threads = threads
                    self.heartbeat(force=True)
                self.heartbeat()
                did = self.maybe_scan(s) or did
                did = self.meta_batch(threads) or did
                did = self.embed_manual(s, threads) or did
                if not did:
                    did = self.faces_batch(s, threads)
                if not did:
                    did = self.maybe_dupes(s) or self.maybe_xmp(s)
                self.daily(s)
                global _log_files
                _log_files = s.get("log_detail", "files") == "files"
                self.summary(s)
                if not did:
                    time.sleep(3)
            except Exception as e:
                log.exception("Worker error: %s", e)
                time.sleep(10)


def _eta(sec: float) -> str:
    if sec < 3600:
        return f"{max(1, round(sec / 60))} min"
    if sec < 172800:
        return f"{sec / 3600:.1f} hours"
    return f"{sec / 86400:.1f} days"


def save_face_crop(face_id: int, rgb: np.ndarray, box) -> None:
    ih, iw = rgb.shape[:2]
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * 1.35
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    l, t = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    r, b = int(min(iw, cx + side / 2)), int(min(ih, cy + side / 2))
    crop = Image.fromarray(rgb[t:b, l:r])
    crop.thumbnail((200, 200), Image.Resampling.LANCZOS)
    dest = media.face_crop_path(face_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest, "WEBP", quality=82)


def _setup_logging():
    config.ensure_dirs()
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(config.LOG_DIR / "worker.log", maxBytes=2_000_000, backupCount=2))
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)


def main():
    _setup_logging()
    try:
        os.nice(19)
    except Exception:
        pass
    db.init()
    Worker().run()


if __name__ == "__main__":
    main()
