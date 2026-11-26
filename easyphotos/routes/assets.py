"""Browsing photos: timeline, details, images, video, downloads, map,
calendar, tags, albums, search. Changes are admin-only."""
import datetime as dt
import io
import os
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .. import db, geocode, media, recycle, settings, xmp
from ..security import User, admin_user, audit, current_user, download_user
from .common import (MIME, asset_path, build_filter, cached_file, get_asset_for, ids_param, library_clause,
                     ranged_file, visible_ids)

router = APIRouter(prefix="/api")


def _item(r, favs=None):
    w, h = media.edited_size(r["width"], r["height"], r["edit_rot"], r["edit_crop"])
    return {
        "id": r["id"], "t": r["taken_at"], "w": w, "h": h,
        "k": "v" if r["kind"] == "video" else "i", "d": r["duration"], "c": r["city"],
        "v": r["thumb_ver"], "l": 1 if r["live_video_id"] else 0,
        "f": 1 if favs and r["id"] in favs else 0,
    }


def _favs(user, ids):
    if not ids:
        return set()
    return {r["asset_id"] for r in db.q(
        f"SELECT asset_id FROM favorites WHERE user_id=? AND asset_id IN ({','.join('?' * len(ids))})", [user.id] + ids)}


# ------------------------------------------------------------ lists

# How photo lists can be sorted: field -> SQL. Date taken is the default.
SORTS = {
    "taken": "a.taken_ts",
    "added": "a.added_at",
    "size": "a.size",
    "name": "a.filename COLLATE NOCASE",
    "type": "(a.kind = 'video')",
}


@router.get("/assets")
def list_assets(request: Request, user: User = Depends(current_user)):
    """Photos in pages, newest first by default. Filters: see build_filter().
    sort: taken | added | size | name | type, order: asc | desc."""
    p = dict(request.query_params)
    where, args = build_filter(user, p)
    limit = max(1, min(int(p.get("limit", 300)), 1000))
    order = "ASC" if p.get("order") == "asc" else "DESC"
    sort = p.get("sort") if p.get("sort") in SORTS else "taken"
    total = None
    if p.get("count") == "1":
        total = db.val(f"SELECT COUNT(*) FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where}", args, default=0)
    cols = ("a.id, a.taken_at, a.taken_ts, a.width, a.height, a.kind, a.duration, a.city, a.thumb_ver, a.live_video_id, "
            "a.edit_rot, a.edit_crop, a.added_at, a.size, a.filename")
    if sort == "taken":
        if p.get("before_ts"):
            bts, bid = float(p["before_ts"]), int(p.get("before_id", 2**62))
            op = ">" if order == "ASC" else "<"
            where += f" AND (a.taken_ts {op} ? OR (a.taken_ts = ? AND a.id {op} ?))"
            args = args + [bts, bts, bid]
        rows = db.q(f"SELECT {cols} FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where} "
                    f"ORDER BY a.taken_ts {order}, a.id {order} LIMIT ?", args + [limit + 1])
    else:
        # Other orders page by position. Within the same size/name/type: newest photo first.
        offset = max(0, int(p.get("offset", 0)))
        rows = db.q(f"SELECT {cols} FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where} "
                    f"ORDER BY {SORTS[sort]} {order}, a.taken_ts DESC, a.id DESC LIMIT ? OFFSET ?", args + [limit + 1, offset])
    more = len(rows) > limit
    rows = rows[:limit]
    favs = _favs(user, [r["id"] for r in rows])
    items = [_item(r, favs) for r in rows]
    if sort == "added":
        # grouped by the day the photo was added to the library
        for it, r in zip(items, rows):
            it["g"] = dt.datetime.fromtimestamp(r["added_at"]).strftime("%Y-%m-%d") if r["added_at"] else ""
    elif sort != "taken":
        for it, r in zip(items, rows):
            it["g"] = "all"
            if sort == "size":
                it["sz"] = r["size"]
    if not more or not rows:
        nxt = None
    elif sort == "taken":
        nxt = {"before_ts": rows[-1]["taken_ts"], "before_id": rows[-1]["id"]}
    else:
        nxt = {"offset": int(p.get("offset", 0)) + len(rows)}
    return {"items": items, "next": nxt, "total": total, "sort": sort, "order": order.lower()}


@router.get("/assets/months")
def months(request: Request, user: User = Depends(current_user)):
    where, args = build_filter(user, dict(request.query_params))
    rows = db.q(f"SELECT substr(a.taken_at,1,7) AS m, COUNT(*) AS n, MAX(a.taken_ts) AS ts FROM assets a "
                f"JOIN libraries l ON l.id=a.library_id WHERE {where} GROUP BY m ORDER BY m DESC", args)
    return [{"month": r["m"], "count": r["n"]} for r in rows]


@router.get("/assets/ids")
def all_ids(request: Request, user: User = Depends(current_user)):
    """Every id matching the filters (for 'select all')."""
    where, args = build_filter(user, dict(request.query_params))
    rows = db.q(f"SELECT a.id FROM assets a JOIN libraries l ON l.id=a.library_id WHERE {where} "
                f"ORDER BY a.taken_ts DESC LIMIT 50000", args)
    return [r["id"] for r in rows]


# ------------------------------------------------------------ one photo

@router.get("/assets/{asset_id}")
def asset_detail(asset_id: int, user: User = Depends(current_user)):
    a = get_asset_for(user, asset_id, allow_deleted=True)
    faces = db.q("SELECT f.id, f.x1, f.y1, f.x2, f.y2, f.person_id, f.manual, p.name, p.hidden "
                 "FROM faces f LEFT JOIN persons p ON p.id=f.person_id WHERE f.asset_id=? AND f.rejected=0", (asset_id,))
    tags = db.q("SELECT t.id, t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=? ORDER BY t.name", (asset_id,))
    albums = db.q("SELECT al.id, al.name FROM album_assets aa JOIN albums al ON al.id=aa.album_id WHERE aa.asset_id=?", (asset_id,))
    fav = db.q1("SELECT 1 FROM favorites WHERE user_id=? AND asset_id=?", (user.id, asset_id)) is not None
    out = {
        "id": a["id"], "filename": a["filename"], "kind": a["kind"], "ext": a["ext"],
        "width": a["width"], "height": a["height"], "size": a["size"],
        "taken_at": a["taken_at"], "date_source": a["date_source"],
        "lat": a["lat"], "lon": a["lon"], "city": a["city"], "state": a["state"], "country": a["country"],
        "camera_make": a["camera_make"], "camera_model": a["camera_model"], "lens": a["lens"],
        "fnumber": a["fnumber"], "exposure": a["exposure"], "focal": a["focal"], "iso": a["iso"],
        "duration": a["duration"], "video_codec": a["video_codec"],
        "description": a["description"], "favorite": fav, "hidden": bool(a["hidden"]),
        "live": bool(a["live_video_id"]), "v": a["thumb_ver"], "deleted": a["deleted_at"] is not None,
        "edit": {"rot": a["edit_rot"] or 0, "crop": [float(x) for x in a["edit_crop"].split(",")] if a["edit_crop"] else None},
        "faces": [{"id": f["id"], "x1": f["x1"], "y1": f["y1"], "x2": f["x2"], "y2": f["y2"],
                   "person_id": f["person_id"] if (user.is_admin or not f["hidden"]) else None,
                   "name": f["name"] if (user.is_admin or not f["hidden"]) else None,
                   "manual": bool(f["manual"])} for f in faces],
        "tags": [dict(t) for t in tags],
        "albums": [dict(x) for x in albums],
    }
    if user.is_admin:
        lib = db.q1("SELECT name FROM libraries WHERE id=?", (a["library_id"],))
        out["library"] = lib["name"] if lib else ""
        out["path"] = a["path"]
        out["error"] = a["error"]
    return out


@router.get("/assets/{asset_id}/thumb")
def thumb(asset_id: int, user: User = Depends(current_user)):
    get_asset_for(user, asset_id, allow_deleted=True)
    p = media.thumb_path(asset_id, "t")
    return cached_file(p, "image/webp" if p.suffix == ".webp" else "image/jpeg")


@router.get("/assets/{asset_id}/preview")
def preview(asset_id: int, orig: int = 0, user: User = Depends(current_user)):
    """The photo as shown (cropped/rotated if edited). orig=1: as taken (for the editor)."""
    a = get_asset_for(user, asset_id, allow_deleted=True)
    p = media.thumb_path(asset_id, "p")
    if not orig and (a["edit_rot"] or a["edit_crop"]):
        e = media.thumb_path(asset_id, "e")
        if e.exists():
            p = e
    return cached_file(p, "image/webp" if p.suffix == ".webp" else "image/jpeg")


@router.patch("/assets/{asset_id}/edit")
def edit_photo(asset_id: int, body: dict, user: User = Depends(admin_user)):
    """Crop & rotate. Only instructions are saved; the photo file is never touched.
    body: {rot: 0|90|180|270, crop: [x, y, w, h] (0-1, after rotating) or null}"""
    a = get_asset_for(user, asset_id)
    if a["kind"] == "video":
        raise HTTPException(400, "Videos can't be cropped or rotated.")
    rot = int(body.get("rot") or 0) % 360
    if rot not in (0, 90, 180, 270):
        raise HTTPException(400, "Rotation must be 0, 90, 180 or 270.")
    crop = body.get("crop")
    crop_text = None
    if crop:
        try:
            x, y, w, h = (min(1.0, max(0.0, float(v))) for v in crop)
        except (TypeError, ValueError):
            raise HTTPException(400, "Crop must be four numbers.")
        w, h = min(w, 1 - x), min(h, 1 - y)
        if w < 0.02 or h < 0.02:
            raise HTTPException(400, "That crop is too small.")
        if not (x < 0.001 and y < 0.001 and w > 0.999 and h > 0.999):  # the whole photo = no crop
            crop_text = f"{x:.5f},{y:.5f},{w:.5f},{h:.5f}"
    db.ex("UPDATE assets SET edit_rot=?, edit_crop=?, thumb_ver=thumb_ver+1 WHERE id=?", (rot, crop_text, asset_id))
    try:
        media.make_edited(asset_id, rot, crop_text)
    except Exception as e:
        raise HTTPException(500, f"Couldn't make the edited picture: {e}")
    audit(None, user, "edited photo %d (rotate %d, crop %s)", asset_id, rot, crop_text or "none")
    r = db.q1("SELECT * FROM assets WHERE id=?", (asset_id,))
    return {"item": _item(r), "edit": {"rot": rot, "crop": [float(v) for v in crop_text.split(",")] if crop_text else None}}


@router.get("/assets/{asset_id}/edited")
def download_edited(asset_id: int, user: User = Depends(download_user)):
    """Full-size cropped/rotated copy, made on the fly from the untouched original."""
    a = get_asset_for(user, asset_id)
    if a["kind"] == "video":
        raise HTTPException(400, "Videos aren't edited.")
    img = media.open_image(str(asset_path(a)))
    img = media.apply_edit(img.convert("RGB"), a["edit_rot"], a["edit_crop"])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=True)
    stem = os.path.splitext(a["filename"])[0]
    return Response(buf.getvalue(), media_type="image/jpeg",
                    headers={"Content-Disposition": f'attachment; filename="{stem}-edited.jpg"'})


@router.get("/assets/{asset_id}/original")
def original(asset_id: int, request: Request, user: User = Depends(download_user)):
    a = get_asset_for(user, asset_id)
    return ranged_file(request, asset_path(a), MIME.get(a["ext"], "application/octet-stream"), a["filename"], download=True)


@router.get("/assets/{asset_id}/video")
def video(asset_id: int, request: Request, compat: int = 0, user: User = Depends(current_user)):
    a = get_asset_for(user, asset_id)
    if a["kind"] != "video":
        raise HTTPException(400, "Not a video.")
    if compat:
        p = media.compat_video(asset_id, str(asset_path(a)))
        if p is None:
            raise HTTPException(500, "This video could not be converted.")
        return ranged_file(request, p, "video/mp4")
    return ranged_file(request, asset_path(a), MIME.get(a["ext"], "video/mp4"))


@router.get("/assets/{asset_id}/live")
def live_video(asset_id: int, request: Request, compat: int = 0, user: User = Depends(current_user)):
    a = get_asset_for(user, asset_id)
    if not a["live_video_id"]:
        raise HTTPException(404, "Not a Live Photo.")
    v = db.q1("SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (a["live_video_id"],))
    if v is None:
        raise HTTPException(404, "Live video missing.")
    if compat:
        p = media.compat_video(v["id"], str(asset_path(v)))
        if p is None:
            raise HTTPException(500, "Could not convert.")
        return ranged_file(request, p, "video/mp4")
    return ranged_file(request, asset_path(v), MIME.get(v["ext"], "video/quicktime"))


@router.post("/assets/{asset_id}/favorite")
def favorite(asset_id: int, body: dict, user: User = Depends(current_user)):
    """Favorites are personal: they only change your own Favorites list."""
    get_asset_for(user, asset_id)
    if body.get("favorite"):
        db.ex("INSERT OR IGNORE INTO favorites(user_id, asset_id) VALUES (?,?)", (user.id, asset_id))
    else:
        db.ex("DELETE FROM favorites WHERE user_id=? AND asset_id=?", (user.id, asset_id))
    return {"favorite": bool(body.get("favorite"))}


# ------------------------------------------------------------ editing (admin)

def _parse_dt(value: str) -> dt.datetime:
    value = (value or "").strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise HTTPException(400, "Date must look like 2024-07-04 or 2024-07-04T15:30.")


def _tag_id(name: str) -> int:
    name = name.strip()[:60]
    if not name:
        raise HTTPException(400, "Tag can't be empty.")
    row = db.q1("SELECT id FROM tags WHERE name=?", (name,))
    if row:
        return row["id"]
    return db.ex("INSERT INTO tags(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid


def _cleanup_tags():
    db.ex("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM asset_tags)")


@router.patch("/assets/{asset_id}")
def edit_asset(asset_id: int, body: dict, user: User = Depends(admin_user)):
    get_asset_for(user, asset_id)
    cols = {}
    if "description" in body:
        cols["description"] = (body["description"] or "").strip()[:2000] or None
    if "taken_at" in body and body["taken_at"]:
        d = _parse_dt(body["taken_at"])
        cols.update(taken_at=media.fmt_dt(d), taken_ts=media.to_ts(d), date_source="manual")
    if body.get("clear_location"):
        cols.update(lat=None, lon=None, city=None, state=None, country=None, loc_source="manual")
    elif body.get("lat") is not None and body.get("lon") is not None:
        lat, lon = float(body["lat"]), float(body["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(400, "That map spot is not valid.")
        city, state, country = geocode.lookup(lat, lon)
        cols.update(lat=lat, lon=lon, city=city, state=state, country=country, loc_source="manual")
    if "place" in body and isinstance(body["place"], dict):
        pl = body["place"]
        cols.update(city=(pl.get("city") or "").strip() or None, state=(pl.get("state") or "").strip() or None,
                    country=(pl.get("country") or "").strip() or None, loc_source="manual")
    if "camera_model" in body:
        cols["camera_model"] = (body["camera_model"] or "").strip()[:100] or None
    if cols:
        keys = list(cols)
        db.ex(f"UPDATE assets SET {', '.join(k + '=?' for k in keys)} WHERE id=?", [cols[k] for k in keys] + [asset_id])
    if "tags" in body and isinstance(body["tags"], list):
        names = list(dict.fromkeys(str(t).strip() for t in body["tags"] if str(t).strip()))[:50]
        ids = [_tag_id(n) for n in names]
        with db.tx() as c:
            c.execute("DELETE FROM asset_tags WHERE asset_id=?", (asset_id,))
            c.executemany("INSERT OR IGNORE INTO asset_tags(asset_id, tag_id, added_at) VALUES (?,?,?)",
                          [(asset_id, t, time.time()) for t in ids])
        _cleanup_tags()
    xmp.mark_dirty([asset_id])
    audit(None, user, "edited info for photo %d (%s)", asset_id, ", ".join(k for k in body if k != "tags") or "tags")
    return asset_detail(asset_id, user)


@router.post("/assets/bulk")
def bulk(body: dict, user: User = Depends(admin_user)):
    ids = visible_ids(user, ids_param(body))
    action = body.get("action")
    if not ids:
        raise HTTPException(400, "No photos selected.")
    audit(None, user, "%s on %d photo%s", action, len(ids), "" if len(ids) == 1 else "s")
    now = time.time()
    ph = lambda n: ",".join("?" * n)  # noqa: E731
    if action in ("hide", "unhide"):
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            db.ex(f"UPDATE assets SET hidden=? WHERE id IN ({ph(len(ch))})", [1 if action == "hide" else 0] + ch)
        return {"ok": True, "count": len(ids)}
    if action == "delete":
        if not settings.get("allow_delete"):
            raise HTTPException(403, "Deleting is turned off in Admin settings.")
        res = recycle.delete_assets(ids)
        return {"ok": not res["errors"], **res}
    if action in ("add_tag", "remove_tag"):
        name = (body.get("tag") or "").strip()
        if action == "add_tag":
            tid = _tag_id(name)
            db.exmany("INSERT OR IGNORE INTO asset_tags(asset_id, tag_id, added_at) VALUES (?,?,?)", [(a, tid, now) for a in ids])
        else:
            row = db.q1("SELECT id FROM tags WHERE name=?", (name,))
            if row:
                db.exmany("DELETE FROM asset_tags WHERE asset_id=? AND tag_id=?", [(a, row["id"]) for a in ids])
                _cleanup_tags()
        xmp.mark_dirty(ids)
        return {"ok": True, "count": len(ids)}
    if action in ("add_album", "remove_album"):
        album_id = body.get("album_id")
        if action == "add_album" and not album_id:
            name = (body.get("album_name") or "").strip()[:100]
            if not name:
                raise HTTPException(400, "Album needs a name.")
            album_id = db.ex("INSERT INTO albums(name, created_at) VALUES (?,?)", (name, now)).lastrowid
        album_id = int(album_id)
        if action == "add_album":
            db.exmany("INSERT OR IGNORE INTO album_assets(album_id, asset_id, added_at) VALUES (?,?,?)", [(album_id, a, now) for a in ids])
        else:
            db.exmany("DELETE FROM album_assets WHERE album_id=? AND asset_id=?", [(album_id, a) for a in ids])
        return {"ok": True, "album_id": album_id, "count": len(ids)}
    if action in ("quality_ok", "quality_reset"):
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            db.ex(f"UPDATE assets SET quality_ok=? WHERE id IN ({ph(len(ch))})", [1 if action == "quality_ok" else 0] + ch)
        return {"ok": True, "count": len(ids)}
    if action == "fix_dates":
        mode = body.get("mode")
        rows = db.q(f"SELECT id, taken_ts FROM assets WHERE id IN ({ph(len(ids))}) ORDER BY taken_ts, id", ids) if len(ids) <= 900 else \
            sorted((r for i in range(0, len(ids), 500) for r in db.q(
                f"SELECT id, taken_ts FROM assets WHERE id IN ({ph(len(ids[i:i + 500]))})", ids[i:i + 500])),
                key=lambda r: (r["taken_ts"] or 0, r["id"]))
        updates = []
        if mode == "set":
            try:
                y = int(body["year"])
                mo = int(body.get("month") or 1)
                d = int(body.get("day") or 1)
                h = int(body.get("hour") if body.get("hour") not in (None, "") else 12)
                mi = int(body.get("minute") or 0)
                base = dt.datetime(y, mo, d, h, mi, 0)
            except (KeyError, ValueError, TypeError):
                raise HTTPException(400, "Pick a valid date.")
            # Keep their order by spacing them one second apart.
            for i, r in enumerate(rows):
                nd = base + dt.timedelta(seconds=i)
                updates.append((media.fmt_dt(nd), media.to_ts(nd), r["id"]))
        elif mode == "shift":
            secs = int(body.get("seconds") or 0)
            if not secs:
                raise HTTPException(400, "Choose how much to shift by.")
            for r in rows:
                nd = dt.datetime.fromtimestamp((r["taken_ts"] or 0) + secs, dt.timezone.utc).replace(tzinfo=None)
                updates.append((media.fmt_dt(nd), media.to_ts(nd), r["id"]))
        else:
            raise HTTPException(400, "Unknown date fix.")
        db.exmany("UPDATE assets SET taken_at=?, taken_ts=?, date_source='manual' WHERE id=?", updates)
        xmp.mark_dirty(ids)
        return {"ok": True, "count": len(updates)}
    raise HTTPException(400, "Unknown action.")


# ------------------------------------------------------------ downloads

_zip_tokens: dict[str, dict] = {}
_zip_lock = threading.Lock()


@router.post("/download/prepare")
def download_prepare(body: dict, user: User = Depends(download_user)):
    ids = visible_ids(user, ids_param(body, limit=20000))
    if not ids:
        raise HTTPException(400, "No photos selected.")
    token = secrets.token_urlsafe(24)
    with _zip_lock:
        now = time.time()
        for k in [k for k, v in _zip_tokens.items() if v["exp"] < now]:
            _zip_tokens.pop(k, None)
        _zip_tokens[token] = {"user": user.id, "ids": ids, "exp": now + 900}
    audit(None, user, "downloaded %d photos as a zip", len(ids))
    return {"url": f"/api/download/zip/{token}", "count": len(ids)}


@router.get("/download/zip/{token}")
def download_zip(token: str, user: User = Depends(download_user)):
    with _zip_lock:
        job = _zip_tokens.get(token)
    if not job or job["user"] != user.id or job["exp"] < time.time():
        raise HTTPException(404, "This download link has expired. Select the photos again.")
    from zipstream import ZIP_STORED, ZipStream
    zs = ZipStream(compress_type=ZIP_STORED, sized=False)
    used = set()
    ids = list(job["ids"])
    extra = [r["live_video_id"] for r in db.q(
        f"SELECT live_video_id FROM assets WHERE live_video_id IS NOT NULL AND id IN ({','.join('?' * min(len(ids), 900))})",
        ids[:900])]
    for aid in ids + extra:
        a = db.q1("SELECT a.path, a.filename, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (aid,))
        if a is None:
            continue
        p = os.path.join(a["lib_path"], a["path"])
        if not os.path.exists(p):
            continue
        name = a["filename"]
        stem, ext = os.path.splitext(name)
        n = 1
        while name.lower() in used:
            name = f"{stem} ({n}){ext}"
            n += 1
        used.add(name.lower())
        zs.add_path(p, arcname=name)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    return StreamingResponse(zs, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="EasyPhotos-{stamp}.zip"'})


# ------------------------------------------------------------ map, places, calendar

@router.get("/map/points")
def map_points(request: Request, user: User = Depends(current_user)):
    where, args = build_filter(user, dict(request.query_params))
    rows = db.q(f"SELECT a.id, a.lat, a.lon, a.thumb_ver FROM assets a JOIN libraries l ON l.id=a.library_id "
                f"WHERE {where} AND a.lat IS NOT NULL ORDER BY a.taken_ts DESC", args)
    return [[r["id"], round(r["lat"], 5), round(r["lon"], 5), r["thumb_ver"]] for r in rows]


@router.get("/places")
def places(request: Request, user: User = Depends(current_user)):
    where, args = build_filter(user, dict(request.query_params))
    rows = db.q(f"SELECT a.city, a.state, a.country, COUNT(*) AS n, MAX(a.id) AS cover FROM assets a "
                f"JOIN libraries l ON l.id=a.library_id WHERE {where} AND a.city IS NOT NULL "
                f"GROUP BY a.city, a.state, a.country ORDER BY n DESC LIMIT 500", args)
    return [dict(r) for r in rows]


@router.get("/calendar/{year}/{month}")
def calendar(year: int, month: int, request: Request, user: User = Depends(current_user)):
    where, args = build_filter(user, dict(request.query_params))
    prefix = f"{year:04d}-{month:02d}"
    rows = db.q(f"SELECT substr(a.taken_at,9,2) AS d, COUNT(*) AS n, MAX(a.id) AS cover FROM assets a "
                f"JOIN libraries l ON l.id=a.library_id WHERE {where} AND substr(a.taken_at,1,7)=? GROUP BY d",
                args + [prefix])
    return [{"day": int(r["d"]), "count": r["n"], "cover": r["cover"]} for r in rows]


@router.get("/memories")
def memories(user: User = Depends(current_user)):
    today = dt.date.today()
    where, args = build_filter(user, {"onthisday": today.strftime("%m-%d")})
    rows = db.q(f"SELECT a.id, substr(a.taken_at,1,4) AS y FROM assets a JOIN libraries l ON l.id=a.library_id "
                f"WHERE {where} ORDER BY a.taken_ts DESC LIMIT 300", args)
    years = {}
    for r in rows:
        years.setdefault(r["y"], []).append(r["id"])
    return [{"year": int(y), "ids": ids[:12], "count": len(ids)} for y, ids in years.items()]


# ------------------------------------------------------------ tags & albums

@router.get("/tags")
def tags(user: User = Depends(current_user)):
    lc, la = library_clause(user)
    rows = db.q("SELECT t.id, t.name, COUNT(a.id) AS n, MAX(a.id) AS cover, "
                "MAX(a.taken_ts) AS newest, MIN(a.taken_ts) AS oldest, COALESCE(SUM(a.size), 0) AS bytes FROM tags t "
                "JOIN asset_tags at ON at.tag_id=t.id JOIN assets a ON a.id=at.asset_id JOIN libraries l ON l.id=a.library_id "
                f"WHERE a.deleted_at IS NULL AND a.hidden=0 AND l.enabled=1{lc} GROUP BY t.id ORDER BY n DESC", la)
    return [dict(r) for r in rows]


@router.patch("/tags/{tag_id}")
def rename_tag(tag_id: int, body: dict, user: User = Depends(admin_user)):
    name = (body.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "Tag needs a name.")
    other = db.q1("SELECT id FROM tags WHERE name=? AND id<>?", (name, tag_id))
    ids = [r["asset_id"] for r in db.q("SELECT asset_id FROM asset_tags WHERE tag_id=?", (tag_id,))]
    if other:  # merge into the existing tag
        db.ex("INSERT OR IGNORE INTO asset_tags(asset_id, tag_id, added_at) SELECT asset_id, ?, added_at FROM asset_tags WHERE tag_id=?",
              (other["id"], tag_id))
        db.ex("DELETE FROM tags WHERE id=?", (tag_id,))
        tag_id = other["id"]
    else:
        db.ex("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
    xmp.mark_dirty(ids)
    return {"id": tag_id, "name": name}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, user: User = Depends(admin_user)):
    ids = [r["asset_id"] for r in db.q("SELECT asset_id FROM asset_tags WHERE tag_id=?", (tag_id,))]
    db.ex("DELETE FROM tags WHERE id=?", (tag_id,))
    xmp.mark_dirty(ids)
    return {"ok": True}


@router.get("/albums")
def albums(user: User = Depends(current_user)):
    lc, la = library_clause(user)
    rows = db.q("SELECT al.id, al.name, al.created_at, COUNT(a.id) AS n, MAX(a.id) AS cover, "
                "MAX(a.taken_ts) AS newest, MIN(a.taken_ts) AS oldest, COALESCE(SUM(a.size), 0) AS bytes FROM albums al "
                "LEFT JOIN album_assets aa ON aa.album_id=al.id "
                "LEFT JOIN assets a ON a.id=aa.asset_id AND a.deleted_at IS NULL AND a.hidden=0" + lc +
                " GROUP BY al.id ORDER BY al.created_at DESC", la)
    out = [dict(r) for r in rows]
    if not user.is_admin:
        out = [x for x in out if x["n"]]
    return out


@router.post("/albums")
def create_album(body: dict, user: User = Depends(admin_user)):
    name = (body.get("name") or "").strip()[:100]
    if not name:
        raise HTTPException(400, "Album needs a name.")
    aid = db.ex("INSERT INTO albums(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid
    return {"id": aid, "name": name}


@router.patch("/albums/{album_id}")
def rename_album(album_id: int, body: dict, user: User = Depends(admin_user)):
    name = (body.get("name") or "").strip()[:100]
    if not name:
        raise HTTPException(400, "Album needs a name.")
    db.ex("UPDATE albums SET name=? WHERE id=?", (name, album_id))
    return {"id": album_id, "name": name}


@router.delete("/albums/{album_id}")
def delete_album(album_id: int, user: User = Depends(admin_user)):
    """Deletes the album only; the photos stay in your library."""
    db.ex("DELETE FROM albums WHERE id=?", (album_id,))
    return {"ok": True}


# ------------------------------------------------------------ search suggestions

@router.get("/search/smart")
def smart_search(q: str, offset: int = 0, limit: int = 120, user: User = Depends(current_user)):
    """Photos that look like the words ("fireworks", "kids at the beach"), best first."""
    from .. import clip, settings
    q = (q or "").strip()[:200]
    ready = db.val("SELECT COUNT(*) FROM clip_embeddings", default=0)
    left = db.val("SELECT COUNT(*) FROM assets WHERE clip_status=0 AND meta_status=1 AND is_live_part=0 AND deleted_at IS NULL", default=0)
    base = {"enabled": bool(settings.get("smart_search_enabled")) and clip.available(), "ready": ready, "left": left}
    if not q or not base["enabled"] or not ready:
        return {**base, "items": [], "total": 0}
    found = clip.matching_ids(q)  # real matches only, best first
    allowed = set(visible_ids(user, found))
    ids = [i for i in found if i in allowed]
    page = ids[offset:offset + limit]
    rows = {}
    if page:
        rows = {r["id"]: r for r in db.q(f"SELECT a.* FROM assets a WHERE a.id IN ({','.join('?' * len(page))}) "
                                         f"AND a.is_live_part=0 AND a.meta_status=1" + ("" if user.is_admin else " AND a.hidden=0"), page)}
    favs = _favs(user, list(rows))
    return {**base, "items": [_item(rows[i], favs) for i in page if i in rows], "total": len(ids)}


@router.get("/search/suggest")
def suggest(q: str = "", user: User = Depends(current_user)):
    q = q.strip()
    if not q:
        return {"people": [], "places": [], "tags": [], "albums": []}
    like = f"%{q}%"
    people = db.q("SELECT id, name, cover_face_id FROM persons WHERE name LIKE ? AND hidden=0 AND face_count>0 ORDER BY face_count DESC LIMIT 6", (like,))
    lc, la = library_clause(user)
    places_ = db.q(f"SELECT city, state, COUNT(*) AS n FROM assets a WHERE (city LIKE ? OR state LIKE ?) AND deleted_at IS NULL{lc} "
                   "GROUP BY city, state ORDER BY n DESC LIMIT 6", [like, like] + la)
    tags_ = db.q("SELECT id, name FROM tags WHERE name LIKE ? ORDER BY name LIMIT 6", (like,))
    albums_ = db.q("SELECT id, name FROM albums WHERE name LIKE ? ORDER BY name LIMIT 6", (like,))
    return {"people": [dict(r) for r in people], "places": [dict(r) for r in places_ if r["city"]],
            "tags": [dict(r) for r in tags_], "albums": [dict(r) for r in albums_]}
