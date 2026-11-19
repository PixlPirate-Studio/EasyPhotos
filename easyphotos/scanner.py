"""Finding files in the libraries and reading each one's details."""
import logging
import os
import time
from pathlib import Path

from . import config, db, geocode, media, settings

log = logging.getLogger("easyphotos.scanner")


def library_root(lib) -> Path:
    return Path(lib["path"])


def asset_file(asset, lib=None) -> Path:
    if lib is None:
        lib = db.q1("SELECT path FROM libraries WHERE id=?", (asset["library_id"],))
    return Path(lib["path"]) / asset["path"]


def _walk(root: Path, stats: dict | None = None, on_folder=None, should_stop=None):
    """Yields (relative_path, size, mtime) for every photo/video under root."""
    stats = stats if stats is not None else {}
    stack = [root]
    while stack:
        if should_stop and should_stop():
            stats["stopped"] = True
            return
        d = stack.pop()
        stats["folders"] = stats.get("folders", 0) + 1
        if on_folder:
            on_folder(d)
        try:
            with os.scandir(d) as it:
                for e in it:
                    name = e.name
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if name.startswith(".") or name in config.SKIP_DIRS:
                                continue
                            stack.append(Path(e.path))
                            continue
                        if name.startswith("._") or name.startswith("."):
                            continue
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in config.IMAGE_EXTS and ext not in config.VIDEO_EXTS:
                            continue
                        st = e.stat(follow_symlinks=True)
                        rel = os.path.relpath(e.path, root)
                        yield rel.replace(os.sep, "/"), st.st_size, st.st_mtime
                    except OSError as err:
                        log.warning("Can't read file %s: %s", e.path, err)
                        stats["unreadable"] = stats.get("unreadable", 0) + 1
        except OSError as err:
            log.warning("Can't open folder %s: %s", d, err)
            stats["unreadable"] = stats.get("unreadable", 0) + 1


def scan_library(lib, progress=None, on_folder=None, should_stop=None) -> dict:
    root = library_root(lib)
    stats = {"added": 0, "changed": 0, "removed": 0, "seen": 0, "folders": 0, "unreadable": 0, "missing": False, "stopped": False}
    if not root.is_dir():
        log.error("Library folder missing inside the container: %s (check the Path mapping in Unraid)", root)
        stats["missing"] = True
        db.ex("UPDATE libraries SET scan_message=? WHERE id=?", (f"Folder {root} was not found inside the container. Check the Path mapping in Unraid.", lib["id"]))
        return stats
    if not any(True for _ in os.scandir(root)):
        log.warning("Library folder %s is empty. Is the Host Path in Unraid pointing at the right share?", root)
    log.info("Scanning %s (%s)…", lib["name"], root)
    db.ex("UPDATE libraries SET scan_started_at=?, scan_found=0, scan_stopped=0 WHERE id=?", (time.time(), lib["id"]))
    last_progress = time.time()
    existing = {r["path"]: r for r in db.q(
        "SELECT id, path, size, mtime, deleted_at FROM assets WHERE library_id=?", (lib["id"],))}
    seen = set()
    now = time.time()
    batch_new, batch_changed = [], []

    def flush():
        if batch_new:
            db.exmany(
                "INSERT OR IGNORE INTO assets(library_id, path, folder, filename, ext, kind, size, mtime, added_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)", batch_new)
            batch_new.clear()
        if batch_changed:
            db.exmany("UPDATE assets SET size=?, mtime=?, meta_status=0, face_status=0, quick_hash=NULL, full_hash=NULL, "
                      "thumb_ver=thumb_ver+1 WHERE id=?", batch_changed)
            for _, _, aid in batch_changed:
                db.ex("DELETE FROM faces WHERE asset_id=? AND manual=0 AND confirmed=0", (aid,))
            batch_changed.clear()

    for rel, size, mtime in _walk(root, stats, on_folder, should_stop):
        seen.add(rel)
        stats["seen"] += 1
        old = existing.get(rel)
        if old is None:
            name = rel.rsplit("/", 1)[-1]
            folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
            ext = os.path.splitext(name)[1].lower()
            kind = "video" if ext in config.VIDEO_EXTS else "image"
            batch_new.append((lib["id"], rel, folder, name, ext, kind, size, mtime, now))
            stats["added"] += 1
        elif old["size"] != size or abs((old["mtime"] or 0) - mtime) > 1:
            batch_changed.append((size, mtime, old["id"]))
            stats["changed"] += 1
        if len(batch_new) + len(batch_changed) >= 500:
            flush()
        if time.time() - last_progress > 2:
            last_progress = time.time()
            db.ex("UPDATE libraries SET scan_found=? WHERE id=?", (stats["seen"], lib["id"]))
            if progress:
                progress(stats)
    flush()

    if stats["stopped"]:
        # Keep everything found so far; don't remove anything we didn't get to.
        msg = f"Stopped by you after finding {stats['seen']:,} files. Thumbnails keep being made for those. Press Resume scan to continue."
        db.ex("UPDATE libraries SET scan_started_at=NULL, scan_found=?, scan_message=?, scan_stopped=1 WHERE id=?",
              (stats["seen"], msg, lib["id"]))
        log.info("Scan of %s stopped by admin after %d files", lib["name"], stats["seen"])
        return stats

    if stats["seen"] == 0 and existing:
        # Don't wipe the library if the folder suddenly looks empty (drive not mounted, etc.)
        log.error("%s looks empty now but had %d photos before. Skipping removal in case the drive isn't mounted.",
                  root, len(existing))
        db.ex("UPDATE libraries SET last_scan_at=?, scan_found=?, scan_message=? WHERE id=?",
              (time.time(), 0, "Folder looks empty now, so nothing was removed. Is the drive mounted?", lib["id"]))
        return stats
    gone = [r["id"] for p, r in existing.items() if p not in seen and r["deleted_at"] is None]
    for aid in gone:
        media.remove_derivatives(aid)
    if gone:
        with db.tx() as c:
            c.executemany("DELETE FROM assets WHERE id=?", [(a,) for a in gone])
    stats["removed"] = len(gone)
    msg = f"{stats['added']:,} new, {stats['changed']:,} changed, {stats['removed']:,} removed"
    if stats["unreadable"]:
        msg += f" · {stats['unreadable']:,} couldn't be read (see Logs)"
    elif stats["seen"] == 0:
        msg += " · folder looks empty. Is the Host Path in Unraid right?"
    db.ex("UPDATE libraries SET last_scan_at=?, scan_found=?, scan_message=? WHERE id=?",
          (time.time(), stats["seen"], msg, lib["id"]))
    log.info("Scanned %s: %s", lib["name"], stats)
    return stats


def process_meta(asset_id: int) -> dict:
    """Reads one file: details, thumbnails, duplicate fingerprint, place name.
    Returns the columns to update (runs in a worker thread, no DB writes)."""
    a = db.q1("SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (asset_id,))
    if a is None:
        return {}
    path = str(Path(a["lib_path"]) / a["path"])
    out = {"meta_status": 1, "error": None}
    try:
        if a["kind"] == "video":
            meta = media.read_video_meta(path)
            img = media.video_poster(path, meta.get("duration"))
            if img is None:
                raise RuntimeError("Could not read a frame from this video")
            if "width" not in meta:
                meta["width"], meta["height"] = img.size
        else:
            meta = media.read_image_meta(path)
            img = media.open_image(path, draft_size=settings.get("preview_size"))
            meta["width"], meta["height"] = img.size
        out["dhash"] = media.make_image_thumbs(asset_id, img)
        img.close()
        for k in ("width", "height", "camera_make", "camera_model", "lens", "fnumber", "exposure", "focal", "iso",
                  "duration", "video_codec"):
            if k in meta:
                out[k] = meta[k]
        if a["date_source"] != "manual":
            taken, source = meta.get("taken"), "exif"
            if taken is None:
                taken, source = media.date_from_filename(a["filename"]), "filename"
            if taken is None:
                import datetime as dt
                taken, source = dt.datetime.fromtimestamp(a["mtime"]), "file"
            out["taken_at"] = media.fmt_dt(taken)
            out["taken_ts"] = media.to_ts(taken)
            out["date_source"] = source
        if a["loc_source"] != "manual" and "lat" in meta:
            out["lat"], out["lon"] = meta["lat"], meta["lon"]
            out["loc_source"] = "exif"
            if settings.get("places_enabled"):
                out["city"], out["state"], out["country"] = geocode.lookup(meta["lat"], meta["lon"])
    except Exception as e:
        log.warning("Could not read %s: %s", path, e)
        out = {"meta_status": -1, "error": str(e)[:300]}
        if a["taken_ts"] is None:
            import datetime as dt
            d = dt.datetime.fromtimestamp(a["mtime"])
            out["taken_at"], out["taken_ts"], out["date_source"] = media.fmt_dt(d), media.to_ts(d), "file"
    return out


def save_meta(asset_id: int, cols: dict) -> None:
    if not cols:
        return
    keys = list(cols.keys())
    db.ex(f"UPDATE assets SET {', '.join(k + '=?' for k in keys)} WHERE id=?", [cols[k] for k in keys] + [asset_id])


LIVE_MAX_SECONDS = 6.0


def _like_escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def pair_live_photos(video_ids=None) -> int:
    """Hides the short video half of iPhone Live Photos and links it to its photo,
    so the photo shows in the grid and the motion plays when you open it.
    video_ids: only look at these (None = check every unpaired short video)."""
    sql = ("SELECT id, library_id, folder, filename FROM assets WHERE kind='video' AND meta_status=1 AND is_live_part=0 "
           "AND deleted_at IS NULL AND duration IS NOT NULL AND duration <= ? AND ext IN ('.mov','.mp4')")
    args = [LIVE_MAX_SECONDS]
    if video_ids is not None:
        ids = [int(i) for i in video_ids]
        if not ids:
            return 0
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args += ids
    paired = 0
    for v in db.q(sql, args):
        stem = os.path.splitext(v["filename"])[0]
        if stem.upper().endswith("_HEVC"):
            stem = stem[:-5]
        candidates = [stem]
        if stem.upper().startswith("IMG_") and not stem.upper().startswith("IMG_E"):
            candidates.append("IMG_E" + stem[4:])  # edited copy of the still
        img = None
        for cand in candidates:
            img = db.q1(
                "SELECT id FROM assets WHERE library_id=? AND folder=? AND kind='image' AND deleted_at IS NULL "
                "AND filename LIKE ? ESCAPE '\\' AND live_video_id IS NULL "
                "ORDER BY CASE ext WHEN '.heic' THEN 0 WHEN '.heif' THEN 0 ELSE 1 END LIMIT 1",
                (v["library_id"], v["folder"], _like_escape(cand) + ".%"))
            if img:
                break
        if img:
            with db.tx() as c:
                c.execute("UPDATE assets SET live_video_id=? WHERE id=?", (v["id"], img["id"]))
                c.execute("UPDATE assets SET is_live_part=1, face_status=2 WHERE id=?", (v["id"],))
                c.execute("DELETE FROM dup_members WHERE asset_id=?", (v["id"],))
            paired += 1
    return paired


def refresh_places() -> None:
    """Re-computes place names for every photo with GPS (after turning places on)."""
    rows = db.q("SELECT id, lat, lon FROM assets WHERE lat IS NOT NULL")
    with db.tx() as c:
        for r in rows:
            city, state, country = geocode.lookup(r["lat"], r["lon"])
            c.execute("UPDATE assets SET city=?, state=?, country=? WHERE id=?", (city, state, country, r["id"]))
