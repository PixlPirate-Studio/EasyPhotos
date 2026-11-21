"""Finds files in a library that are not photos or videos (system junk,
temp files, documents...) so the admin can look at them and remove them.

Safety first:
- Any photo or video format is never listed, including ones EasyPhotos
  can't show (camera RAW files, Photoshop files, GoPro proxies...).
- Files with an unknown or missing extension are opened and checked: if
  the first bytes look like a photo or video, they're left out.
- Photo sidecar files (.xmp) are never listed, since they hold edits and tags.
- Removing moves files into the library's hidden .recycle folder, so they
  can be put back until the Recycle Bin is emptied."""
import logging
import os
import time
from pathlib import Path

from . import config, db

log = logging.getLogger("easyphotos")

OTHER_DIR = ".recycle/.other-files"

# Every photo and video format we know of, shown by EasyPhotos or not.
MEDIA_EXTS = set(config.IMAGE_EXTS) | set(config.VIDEO_EXTS) | {
    # photos
    ".jfif", ".pjpeg", ".pjp", ".jxl", ".jp2", ".j2k", ".jpf", ".jpx", ".jpm", ".dib", ".ico", ".cur",
    ".svg", ".svgz", ".psd", ".psb", ".xcf", ".kra", ".exr", ".hdr", ".tga", ".pcx", ".ppm", ".pgm",
    ".pbm", ".pnm", ".wdp", ".jxr", ".hdp", ".apng", ".avifs", ".heics", ".heifs", ".thm", ".mpo",
    ".jps", ".pns", ".insp", ".dds", ".emf", ".wmf", ".eps", ".ai", ".cdr", ".afphoto", ".pdn",
    # camera RAW
    ".raw", ".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".orf", ".ori",
    ".rw2", ".rwl", ".raf", ".pef", ".ptx", ".srw", ".x3f", ".3fr", ".fff", ".erf", ".kdc", ".dcr",
    ".dcs", ".mos", ".mrw", ".mef", ".iiq", ".cap", ".eip", ".bay", ".gpr", ".k25", ".nksc",
    # video
    ".qt", ".mp4v", ".mpe", ".mpv", ".m2v", ".m4p", ".mp2", ".ts", ".mts", ".m2t", ".tts", ".mod", ".tod",
    ".vob", ".3g2", ".3gpp", ".ogv", ".ogm", ".dv", ".dif", ".asf", ".flv", ".f4v", ".divx", ".xvid",
    ".rm", ".rmvb", ".wtv", ".dvr-ms", ".insv", ".360", ".lrv", ".lrf", ".braw", ".r3d", ".mxf",
    ".vro", ".hevc", ".h264", ".h265", ".264", ".265", ".avchd", ".bdmv", ".m4s", ".y4m",
}
# Sidecars that belong with photos. Never listed.
KEEP_EXTS = {".xmp"}

SYSTEM_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "ehthumbs.db", "ehthumbs_vista.db", ".localized",
                "icon\r", ".picasa.ini", "picasa.ini", ".nomedia", "zbthumbnail.info", "albumart.jpg.tmp"}
TEMP_EXTS = {".tmp", ".temp", ".part", ".partial", ".crdownload", ".download", ".opdownload", ".bak",
             ".old", ".swp", ".swo", ".!qb", ".!ut", ".dtapart", ".cache", ".lock", ".dmp"}
DOC_EXTS = {".txt", ".text", ".md", ".rtf", ".pdf", ".doc", ".docx", ".odt", ".pages", ".xls", ".xlsx",
            ".csv", ".ods", ".numbers", ".ppt", ".pptx", ".key", ".log", ".nfo", ".html", ".htm",
            ".url", ".webloc", ".lnk", ".ini", ".json", ".xml", ".plist", ".db", ".sqlite", ".info"}
APPLE_EXTS = {".aae"}

KINDS = {
    "system": "System junk (.DS_Store, Thumbs.db, ._ files…)",
    "temp": "Temporary and half-finished files",
    "apple": "iPhone edit files (.AAE)",
    "docs": "Documents and text files",
    "other": "Other files (music, archives, programs…)",
}

# First bytes of photo and video files, used for files with odd extensions.
MAGIC = [b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"II*\x00", b"MM\x00*", b"\x1aE\xdf\xa3",
         b"8BPS", b"\x00\x00\x00\x0cjP  ", b"\xff\x0a", b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3",
         b"FUJIFILMCCD-RAW", b"IIRO", b"IIU\x00", b"\x30\x26\xb2\x75", b"FLV"]


def looks_like_media(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return True  # can't read it, so don't offer to remove it
    if any(head.startswith(m) for m in MAGIC):
        return True
    if head[4:8] == b"ftyp":  # mp4, mov, heic, avif, cr3, 3gp...
        return True
    if head[:4] == b"RIFF" and head[8:12] in (b"WEBP", b"AVI "):
        return True
    return False


def classify(name: str, path: str):
    """Returns the kind of non-photo file, or None if it is (or might be) a photo/video."""
    low = name.lower()
    ext = os.path.splitext(low)[1]
    # "._name.jpg" files are Apple's hidden helper files, not photos, even
    # though the name ends in a photo extension. Their contents get checked below.
    if (ext in MEDIA_EXTS or ext in KEEP_EXTS) and not low.startswith("._"):
        return None
    if low.startswith("._") or low in SYSTEM_NAMES:
        kind = "system"
    elif low.startswith("~$") or low.startswith(".~") or low.endswith("~") or ext in TEMP_EXTS:
        kind = "temp"
    elif ext in APPLE_EXTS:
        kind = "apple"
    elif ext in DOC_EXTS:
        kind = "docs"
    else:
        kind = "other"
    # Anything not clearly named gets its contents checked, so a photo with a
    # wrong or missing extension is never offered for removal.
    if looks_like_media(path):
        return None
    return kind


def find(root: str, limit: int = 20000):
    out = []
    root = os.path.abspath(root)

    def walk(d):
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            return
        for e in entries:
            if len(out) >= limit:
                return
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if e.name in config.SKIP_DIRS or e.name.startswith("."):
                        continue
                    walk(e.path)
                elif e.is_file(follow_symlinks=False):
                    kind = classify(e.name, e.path)
                    if kind:
                        st = e.stat(follow_symlinks=False)
                        out.append({"rel": os.path.relpath(e.path, root).replace(os.sep, "/"), "kind": kind,
                                    "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue

    if os.path.isdir(root):
        walk(root)
    out.sort(key=lambda x: x["rel"].lower())
    return out


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        cand = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not cand.exists():
            return cand
        n += 1


def remove(lib, rel: str):
    """Moves one file into the hidden recycle folder. Checks it again first."""
    root = Path(lib["path"])
    root_real = os.path.realpath(root)
    src = root / rel
    real = os.path.realpath(src)
    if not real.startswith(root_real + os.sep) or src.is_symlink() or not src.is_file():
        return f"{rel}: file is gone"
    kind = classify(src.name, str(src))
    if not kind:
        return f"{rel}: looks like a photo or video, so it was left alone"
    dest = _unique(root / OTHER_DIR / rel)
    try:
        size = src.stat().st_size
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
    except PermissionError:
        return f"{lib['name']} is read-only. Map it as Read/Write in Unraid to remove files."
    except OSError as e:
        return f"{rel}: {e.strerror or e}"
    db.ex("INSERT INTO other_recycle(library_id, path, recycle_path, size, kind, deleted_at) VALUES (?,?,?,?,?,?)",
          (lib["id"], rel, str(dest.relative_to(root)).replace(os.sep, "/"), size, kind, time.time()))
    log.info("ADMIN  moved non-photo file to recycle: %s", src)
    return None


def restore(ids):
    done, errors = 0, []
    for r in db.q(f"SELECT o.*, l.path AS lib_path FROM other_recycle o JOIN libraries l ON l.id=o.library_id "
                  f"WHERE o.id IN ({','.join('?' * len(ids))})", ids):
        root = Path(r["lib_path"])
        src, dest = root / r["recycle_path"], _unique(root / r["path"])
        try:
            if src.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dest)
            db.ex("DELETE FROM other_recycle WHERE id=?", (r["id"],))
            done += 1
        except OSError as e:
            errors.append(f"{r['path']}: {e.strerror or e}")
    return {"restored": done, "errors": errors[:10]}


def purge(ids=None, older_than=None):
    """Deletes removed non-photo files for good."""
    sql, args = "SELECT o.*, l.path AS lib_path FROM other_recycle o JOIN libraries l ON l.id=o.library_id", []
    if ids is not None:
        if not ids:
            return 0
        sql += f" WHERE o.id IN ({','.join('?' * len(ids))})"
        args = list(ids)
    elif older_than is not None:
        sql += " WHERE o.deleted_at < ?"
        args = [older_than]
    n = 0
    for r in db.q(sql, args):
        p = Path(r["lib_path"]) / r["recycle_path"]
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            continue
        db.ex("DELETE FROM other_recycle WHERE id=?", (r["id"],))
        n += 1
        # tidy up the now-empty hidden folders it sat in
        base = Path(r["lib_path"]) / OTHER_DIR
        d = p.parent
        while d != base and base in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent
    return n
