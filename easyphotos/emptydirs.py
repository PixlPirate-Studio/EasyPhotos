"""Finds folders in a library that have nothing in them, and removes them
when the admin asks. A folder counts as empty when it has no files at all,
or only little system files (.DS_Store, Thumbs.db, desktop.ini, ._ files)
and its sub-folders are empty too. The library's own top folder is never
touched, and nothing is ever removed without being checked again first."""
import logging
import os

from . import config

log = logging.getLogger("easyphotos")

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "ehthumbs.db", ".localized"}
JUNK_MAX_BYTES = 64 * 1024  # system files are tiny; anything bigger is kept


def _is_junk(entry: os.DirEntry) -> bool:
    name = entry.name.lower()
    if name not in JUNK_NAMES and not name.startswith("._"):
        return False
    try:
        return entry.stat(follow_symlinks=False).st_size <= JUNK_MAX_BYTES
    except OSError:
        return False


def _check(path: str):
    """Returns (is_empty, empty_subfolder_count, junk_files, found) for one folder.
    found is the list of the top-most empty folders below it when it is not empty."""
    junk, subs_empty, all_empty, found = [], 0, True, []
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        return False, 0, [], []
    for e in entries:
        try:
            if e.is_symlink():
                all_empty = False
            elif e.is_dir(follow_symlinks=False):
                if e.name in config.SKIP_DIRS or e.name.startswith("."):
                    all_empty = False  # leave special and hidden folders alone
                    continue
                empty, n, j, below = _check(e.path)
                if empty:
                    subs_empty += 1 + n
                    junk += j
                    found.append({"path": e.path, "subfolders": n, "junk": [os.path.basename(x) for x in j][:5],
                                  "junk_count": len(j)})
                else:
                    all_empty = False
                    found += below
            elif _is_junk(e):
                junk.append(e.path)
            else:
                all_empty = False
        except OSError:
            all_empty = False
    return all_empty, subs_empty, junk, found


def find(root: str, limit: int = 5000):
    """Top-most empty folders inside root (never root itself)."""
    if not os.path.isdir(root):
        return []
    _, _, _, found = _check(root)
    out = []
    for f in found[:limit]:
        f["rel"] = os.path.relpath(f["path"], root)
        out.append(f)
    out.sort(key=lambda x: x["rel"].lower())
    return out


def remove(root: str, rel: str):
    """Removes one empty folder (and its empty sub-folders and system files).
    Checks again right before removing; returns an error message or None."""
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, rel))
    if target == root_real or not target.startswith(root_real + os.sep):
        return f"{rel}: not inside the library"
    if os.path.islink(os.path.join(root, rel)) or not os.path.isdir(target):
        return f"{rel}: folder is gone or not a folder"
    empty, _, junk, _ = _check(target)
    if not empty:
        return f"{rel}: something was added to it, so it was left alone"
    try:
        for j in junk:
            os.remove(j)
        for dirpath, dirnames, _files in os.walk(target, topdown=False):
            os.rmdir(dirpath)  # rmdir only works on empty folders, an extra safety net
    except OSError as e:
        return f"{rel}: {e.strerror or e}"
    log.info("ADMIN  removed empty folder %s", target)
    return None
