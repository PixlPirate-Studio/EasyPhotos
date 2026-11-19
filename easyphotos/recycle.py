"""Recycle Bin: deleting moves files into a hidden .recycle folder inside the
same library (same drive, so it is instant). Restore moves them back.
Only emptying the bin removes files for good."""
import logging
import os
import shutil
import time
from pathlib import Path

from . import db, media

log = logging.getLogger("easyphotos.recycle")

RECYCLE_DIR = ".recycle"


class RecycleError(Exception):
    pass


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        cand = dest.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def _move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest)
    except OSError:
        shutil.move(str(src), str(dest))


def _ids_with_live(ids):
    out = list(ids)
    for r in db.q(f"SELECT live_video_id FROM assets WHERE id IN ({','.join('?' * len(ids))}) AND live_video_id IS NOT NULL", ids):
        out.append(r["live_video_id"])
    return list(dict.fromkeys(out))


def delete_assets(ids: list[int]) -> dict:
    """Moves files to the Recycle Bin. Returns {'moved': n, 'errors': [...]}"""
    if not ids:
        return {"moved": 0, "errors": []}
    moved, errors = 0, []
    for aid in _ids_with_live(ids):
        a = db.q1("SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (aid,))
        if a is None or a["deleted_at"] is not None:
            continue
        root = Path(a["lib_path"])
        src = root / a["path"]
        dest = _unique(root / RECYCLE_DIR / a["path"])
        try:
            if src.exists():
                _move(src, dest)
            rel = str(dest.relative_to(root)).replace(os.sep, "/")
            db.ex("UPDATE assets SET deleted_at=?, recycle_path=? WHERE id=?", (time.time(), rel, aid))
            moved += 1
        except PermissionError:
            errors.append(f"{a['filename']}: the library folder is read-only. Map it as Read/Write in Unraid to allow deleting.")
        except OSError as e:
            errors.append(f"{a['filename']}: {e}")
    db.ex("DELETE FROM dup_members WHERE asset_id IN (SELECT id FROM assets WHERE deleted_at IS NOT NULL)")
    return {"moved": moved, "errors": errors[:20]}


def restore_assets(ids: list[int]) -> dict:
    restored, errors = 0, []
    for aid in _ids_with_live(ids):
        a = db.q1("SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (aid,))
        if a is None or a["deleted_at"] is None:
            continue
        root = Path(a["lib_path"])
        src = root / (a["recycle_path"] or "")
        dest = root / a["path"]
        try:
            if dest.exists():
                dest = _unique(dest)
            if a["recycle_path"] and src.exists():
                _move(src, dest)
            rel = str(dest.relative_to(root)).replace(os.sep, "/")
            folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
            db.ex("UPDATE assets SET deleted_at=NULL, recycle_path=NULL, path=?, folder=?, filename=? WHERE id=?",
                  (rel, folder, rel.rsplit("/", 1)[-1], aid))
            restored += 1
        except OSError as e:
            errors.append(f"{a['filename']}: {e}")
    return {"restored": restored, "errors": errors[:20]}


def purge_assets(ids: list[int]) -> dict:
    """Deletes files from the Recycle Bin forever."""
    purged, errors = 0, []
    for aid in _ids_with_live(ids):
        a = db.q1("SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (aid,))
        if a is None or a["deleted_at"] is None:
            continue
        try:
            if a["recycle_path"]:
                p = Path(a["lib_path"]) / a["recycle_path"]
                if p.exists():
                    p.unlink()
            media.remove_derivatives(aid)
            for f in db.q("SELECT id FROM faces WHERE asset_id=?", (aid,)):
                try:
                    media.face_crop_path(f["id"]).unlink(missing_ok=True)
                except Exception:
                    pass
            persons = [r["person_id"] for r in db.q("SELECT DISTINCT person_id FROM faces WHERE asset_id=? AND person_id IS NOT NULL", (aid,))]
            db.ex("DELETE FROM assets WHERE id=?", (aid,))
            from .cluster import recompute_person
            for pid in persons:
                recompute_person(pid)
            purged += 1
        except OSError as e:
            errors.append(f"{a['filename']}: {e}")
    return {"purged": purged, "errors": errors[:20]}


def auto_empty(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    ids = [r["id"] for r in db.q("SELECT id FROM assets WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))]
    if ids:
        purge_assets(ids)
    return len(ids)
