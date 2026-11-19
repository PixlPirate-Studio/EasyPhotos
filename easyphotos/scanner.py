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


def _walk(root: Path):
    """Yields (relative_path, size, mtime) for every photo/video under root."""
    stack = [root]
    while stack:
        d = stack.pop()
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
                        log.debug("skip %s: %s", e.path, err)
        except OSError as err:
            log.warning("cannot read folder %s: %s", d, err)


def scan_library(lib, progress=None) -> dict:
    root = library_root(lib)
    stats = {"added": 0, "changed": 0, "removed": 0, "seen": 0}
    if not root.is_dir():
        log.warning("Library folder missing: %s", root)
        return stats
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

    for rel, size, mtime in _walk(root):
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
            if progress:
                progress(stats)
    flush()

    gone = [r["id"] for p, r in existing.items() if p not in seen and r["deleted_at"] is None]
    for aid in gone:
        media.remove_derivatives(aid)
    if gone:
        with db.tx() as c:
            c.executemany("DELETE FROM assets WHERE id=?", [(a,) for a in gone])
    stats["removed"] = len(gone)
    db.ex("UPDATE libraries SET last_scan_at=? WHERE id=?", (time.time(), lib["id"]))
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


def pair_live_photos() -> int:
    """Hides the short video half of iPhone Live Photos and links it to the photo."""
    vids = db.q(
        "SELECT id, library_id, folder, filename FROM assets WHERE kind='video' AND meta_status=1 AND is_live_part=0 "
        "AND deleted_at IS NULL AND duration IS NOT NULL AND duration <= 4.5 AND ext IN ('.mov','.mp4')")
    paired = 0
    for v in vids:
        stem = os.path.splitext(v["filename"])[0]
        img = db.q1(
            "SELECT id FROM assets WHERE library_id=? AND folder=? AND kind='image' AND deleted_at IS NULL "
            "AND (filename LIKE ? ESCAPE '\\') AND live_video_id IS NULL LIMIT 1",
            (v["library_id"], v["folder"], stem.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ".%"))
        if img:
            db.ex("UPDATE assets SET live_video_id=? WHERE id=?", (v["id"], img["id"]))
            db.ex("UPDATE assets SET is_live_part=1 WHERE id=?", (v["id"],))
            paired += 1
    return paired


def refresh_places() -> None:
    """Re-computes place names for every photo with GPS (after turning places on)."""
    rows = db.q("SELECT id, lat, lon FROM assets WHERE lat IS NOT NULL")
    with db.tx() as c:
        for r in rows:
            city, state, country = geocode.lookup(r["lat"], r["lon"])
            c.execute("UPDATE assets SET city=?, state=?, country=? WHERE id=?", (city, state, country, r["id"]))
