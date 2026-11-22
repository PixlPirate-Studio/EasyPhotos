"""Public API for other apps (dashboards, photo frames, Home Assistant...).

Every request needs an API key made in Admin settings > API keys, either in
the "X-API-Key" header or as ?key=... in the address. Each key only sees the
folders you picked for it.
"""
import hashlib
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import db, media
from .common import MIME, asset_path, ranged_file

router = APIRouter(prefix="/api/v1")


def _key(request: Request):
    raw = request.headers.get("x-api-key") or request.query_params.get("key") or ""
    if not raw:
        raise HTTPException(401, "Missing API key. Send it as the X-API-Key header or ?key=")
    row = db.q1("SELECT * FROM api_keys WHERE key_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))
    if row is None:
        raise HTTPException(401, "Unknown API key.")
    if not row["last_used"] or time.time() - row["last_used"] > 60:
        db.ex("UPDATE api_keys SET last_used=? WHERE id=?", (time.time(), row["id"]))
    return row, raw


def _scope(key_row):
    folders = json.loads(key_row["folders"] or "[]")
    where = ["a.deleted_at IS NULL", "a.hidden=0", "a.is_live_part=0", "a.meta_status=1", "l.enabled=1"]
    args = []
    if not key_row["include_videos"]:
        where.append("a.kind='image'")
    if folders:
        parts = []
        for f in folders:
            if f.get("folder"):
                fo = f["folder"]
                parts.append("(a.library_id=? AND (a.folder=? OR a.folder LIKE ? ESCAPE '\\'))")
                args += [f["library_id"], fo, fo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"]
            else:
                parts.append("a.library_id=?")
                args.append(f["library_id"])
        where.append("(" + " OR ".join(parts) + ")")
    return " AND ".join(where), args


def _filters(request: Request, where: str, args: list):
    person = request.query_params.get("person")
    tag = request.query_params.get("tag")
    album = request.query_params.get("album")
    if person:
        where += " AND a.id IN (SELECT f.asset_id FROM faces f JOIN persons p ON p.id=f.person_id WHERE p.name=? AND f.rejected=0)"
        args = args + [person]
    if tag:
        where += " AND a.id IN (SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE t.name=?)"
        args = args + [tag]
    if album:
        where += " AND a.id IN (SELECT aa.asset_id FROM album_assets aa JOIN albums al ON al.id=aa.album_id WHERE al.name=?)"
        args = args + [album]
    orient = request.query_params.get("orientation")
    if orient == "landscape":
        where += " AND a.width > a.height"
    elif orient == "portrait":
        where += " AND a.height > a.width"
    return where, args


def _out(r, base: str, key: str):
    q = f"?key={key}"
    return {
        "id": r["id"], "filename": r["filename"], "type": r["kind"], "taken_at": r["taken_at"],
        "width": r["width"], "height": r["height"],
        "place": ", ".join(x for x in (r["city"], r["state"], r["country"]) if x) or None,
        "description": r["description"],
        "thumbnail_url": f"{base}api/v1/photos/{r['id']}/thumbnail{q}",
        "preview_url": f"{base}api/v1/photos/{r['id']}/preview{q}",
        "original_url": f"{base}api/v1/photos/{r['id']}/original{q}",
    }


@router.get("/random")
def random_photos(request: Request, count: int = 1):
    """Random photos as JSON. Optional: count (1-100), person, tag, album, orientation."""
    key_row, raw = _key(request)
    where, args = _scope(key_row)
    where, args = _filters(request, where, args)
    count = max(1, min(int(count), 100))
    rows = db.q(f"SELECT a.* FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where} ORDER BY RANDOM() LIMIT ?",
                args + [count])
    base = str(request.base_url)
    return {"photos": [_out(r, base, raw) for r in rows]}


@router.get("/random/image")
def random_image(request: Request, size: str = "preview"):
    """One random picture, returned as the image itself (handy for an <img> tag)."""
    key_row, _ = _key(request)
    where, args = _scope(key_row)
    where, args = _filters(request, where + " AND a.kind='image'", args)
    row = db.q1(f"SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where} "
                "ORDER BY RANDOM() LIMIT 1", args)
    if row is None:
        raise HTTPException(404, "No photos in the folders this key can see.")
    return _serve(request, row, size, no_cache=True)


@router.get("/photos/{asset_id}/{size}")
def photo(asset_id: int, size: str, request: Request):
    key_row, _ = _key(request)
    where, args = _scope(key_row)
    row = db.q1(f"SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where} AND a.id=?",
                args + [asset_id])
    if row is None:
        raise HTTPException(404, "Photo not found for this key.")
    return _serve(request, row, size)


def _serve(request: Request, row, size: str, no_cache: bool = False):
    headers = {"Cache-Control": "no-store"} if no_cache else {"Cache-Control": "private, max-age=86400"}
    if size in ("thumbnail", "thumb", "preview"):
        p = media.thumb_path(row["id"], "t" if size.startswith("thumb") else "p")
        if not size.startswith("thumb") and (row["edit_rot"] or row["edit_crop"]):
            e = media.thumb_path(row["id"], "e")
            p = e if e.exists() else p
        if not p.exists():
            raise HTTPException(404, "Not ready yet.")
        return FileResponse(p, media_type="image/webp" if p.suffix == ".webp" else "image/jpeg", headers=headers)
    if size == "original":
        return ranged_file(request, asset_path(row), MIME.get(row["ext"], "application/octet-stream"), row["filename"])
    raise HTTPException(400, "Size must be thumbnail, preview or original.")


@router.get("/info")
def info(request: Request):
    key_row, _ = _key(request)
    where, args = _scope(key_row)
    n = db.val(f"SELECT COUNT(*) FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where}", args, default=0)
    return {"name": key_row["name"], "photos": n}
