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

    # ------------------------------------------------------------ commands
    def handle_commands(self) -> bool:
        rows = db.q("SELECT id, name, args FROM job_commands ORDER BY id")
        if not rows:
            return False
        db.ex("DELETE FROM job_commands WHERE id <= ?", (rows[-1]["id"],))
        for r in rows:
            name = r["name"]
            args = json.loads(r["args"] or "{}")
            log.info("Command: %s %s", name, args)
            if name == "scan":
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
        did = False
        interval = float(s["scan_interval_hours"]) * 3600
        for lib in db.q("SELECT * FROM libraries WHERE enabled=1"):
            due = lib["last_scan_at"] is None or (interval > 0 and time.time() - lib["last_scan_at"] > interval)
            if lib["id"] in self.force_scan or due:
                self.force_scan.discard(lib["id"])
                set_status("scan", "running", f"Looking for new photos in {lib['name']}…")
                stats = scanner.scan_library(lib, progress=lambda st, n=lib["name"]: set_status(
                    "scan", "running", f"Looking for new photos in {n}… {st['seen']:,} photos/videos found in {st['folders']:,} folders"))
                if stats["missing"]:
                    set_status("scan", "error", f"{lib['name']}: folder {lib['path']} was not found inside the container. Check the Path mapping in Unraid.")
                else:
                    msg = (f"{lib['name']}: found {stats['seen']:,} photos/videos in {stats['folders']:,} folders "
                           f"({stats['added']:,} new, {stats['changed']:,} changed, {stats['removed']:,} removed)")
                    if stats["unreadable"]:
                        msg += f". {stats['unreadable']:,} files/folders couldn't be read (permissions?) - see Logs"
                    elif stats["seen"] == 0:
                        msg += ". The folder looks empty - is the Host Path in Unraid right?"
                    set_status("scan", "error" if (stats["unreadable"] or stats["seen"] == 0) else "idle", msg)
                if stats["added"] or stats["changed"] or stats["removed"]:
                    self.dupes_dirty = True
                did = True
        return did

    # ------------------------------------------------------------ metadata + thumbnails
    def meta_batch(self, threads: int) -> bool:
        ids = [r["id"] for r in db.q(
            "SELECT id FROM assets WHERE meta_status=0 AND deleted_at IS NULL ORDER BY taken_ts DESC NULLS FIRST, id DESC LIMIT ?",
            (threads * 8,))]
        if not ids:
            return False
        left = db.val("SELECT COUNT(*) FROM assets WHERE meta_status=0 AND deleted_at IS NULL", default=0)
        set_status("thumbs", "running", f"Making thumbnails… {left:,} to go")
        with ThreadPoolExecutor(max_workers=threads) as pool:
            results = list(pool.map(scanner.process_meta, ids))
        for aid, cols in zip(ids, results):
            scanner.save_meta(aid, cols)
        self.dupes_dirty = True
        if left <= len(ids):
            scanner.pair_live_photos()
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
        for r in rows:
            aid = r["id"]
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
            except Exception as e:
                log.warning("Face scan failed for asset %s: %s", aid, e)
                db.ex("UPDATE assets SET face_status=-1, error=? WHERE id=?", (str(e)[:300], aid))
        cl.finish()
        dt_ = time.time() - t0
        self.face_times.append((len(rows), dt_))
        self.face_times = self.face_times[-30:]
        n = sum(x[0] for x in self.face_times)
        secs = sum(x[1] for x in self.face_times) or 1
        rate = n / secs * 60  # photos per minute
        left = db.val("SELECT COUNT(*) FROM assets WHERE face_status=0 AND deleted_at IS NULL", default=0)
        set_status("faces", "running", f"Finding faces… {left:,} photos to go", rate=rate)
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

    # ------------------------------------------------------------ others
    def maybe_dupes(self, s) -> bool:
        if not s["duplicates_enabled"] or not self.dupes_dirty:
            return False
        if time.time() - self.last_dupes < 600:
            return False
        if db.val("SELECT COUNT(*) FROM assets WHERE meta_status=0 AND deleted_at IS NULL", default=0) > 0:
            return False
        set_status("duplicates", "running", "Checking for duplicates…")
        res = dupes.rebuild()
        self.dupes_dirty = False
        self.last_dupes = time.time()
        set_status("duplicates", "idle", f"{res.get('exact', 0)} exact, {res.get('similar', 0)} look-alike groups")
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
        while True:
            try:
                did = self.handle_commands()
                s = settings.get_all()
                if s["paused"]:
                    set_status("worker", "paused", "Paused by admin")
                    time.sleep(5)
                    continue
                threads = current_threads(s)
                set_status("worker", "running", f"Using {threads} thread{'s' if threads != 1 else ''} at low priority")
                did = self.maybe_scan(s) or did
                did = self.meta_batch(threads) or did
                did = self.embed_manual(s, threads) or did
                if not did:
                    did = self.faces_batch(s, threads)
                if not did:
                    did = self.maybe_dupes(s) or self.maybe_xmp(s)
                self.daily(s)
                if not did:
                    time.sleep(3)
            except Exception as e:
                log.exception("Worker error: %s", e)
                time.sleep(10)


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s %(message)s", handlers=handlers)


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
