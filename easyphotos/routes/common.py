"""Shared helpers: which photos a user may see, and list filters."""
from email.utils import formatdate
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import db
from ..security import User, allowed_library_ids

BASE_VISIBLE = "a.deleted_at IS NULL AND a.is_live_part=0 AND a.meta_status=1 AND l.enabled=1"


def library_clause(user: User, alias: str = "a"):
    ids = allowed_library_ids(user)
    if ids is None:
        return "", []
    if not ids:
        return " AND 0", []
    return f" AND {alias}.library_id IN ({','.join('?' * len(ids))})", list(ids)


def build_filter(user: User, p: dict):
    """Turns query parameters into SQL. Supports person, tag, album, city,
    day, month, year, folder, favorites, hidden, kind and q (search)."""
    where = [BASE_VISIBLE]
    args = []
    lc, la = library_clause(user)
    if lc:
        where.append(lc[5:])
        args += la
    hidden = str(p.get("hidden", "")) == "1" and user.is_admin
    where.append("a.hidden=1" if hidden else "a.hidden=0")
    if p.get("person"):
        where.append("a.id IN (SELECT asset_id FROM faces WHERE person_id=? AND rejected=0)")
        args.append(int(p["person"]))
    if p.get("tag"):
        where.append("a.id IN (SELECT asset_id FROM asset_tags WHERE tag_id=?)")
        args.append(int(p["tag"]))
    if p.get("album"):
        where.append("a.id IN (SELECT asset_id FROM album_assets WHERE album_id=?)")
        args.append(int(p["album"]))
    if p.get("city"):
        where.append("a.city=?")
        args.append(p["city"])
        if p.get("state"):
            where.append("a.state=?")
            args.append(p["state"])
    if p.get("day"):  # YYYY-MM-DD
        where.append("substr(a.taken_at,1,10)=?")
        args.append(p["day"][:10])
    if p.get("month"):  # YYYY-MM
        where.append("substr(a.taken_at,1,7)=?")
        args.append(p["month"][:7])
    if p.get("onthisday"):  # MM-DD
        where.append("substr(a.taken_at,6,5)=? AND substr(a.taken_at,1,4) < strftime('%Y','now','localtime')")
        args.append(p["onthisday"][:5])
    if p.get("library"):
        where.append("a.library_id=?")
        args.append(int(p["library"]))
        if p.get("folder") is not None and p.get("folder") != "":
            f = p["folder"].strip("/")
            where.append("(a.folder=? OR a.folder LIKE ? ESCAPE '\\')")
            args += [f, f.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"]
    if str(p.get("favorites", "")) == "1":
        where.append("a.id IN (SELECT asset_id FROM favorites WHERE user_id=?)")
        args.append(user.id)
    if p.get("kind") in ("image", "video"):
        where.append("a.kind=?")
        args.append(p["kind"])
    q = (p.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        where.append(
            "(a.filename LIKE ? OR a.description LIKE ? OR a.city LIKE ? OR a.state LIKE ? OR a.country LIKE ? "
            "OR a.camera_model LIKE ? "
            "OR a.id IN (SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE t.name LIKE ?) "
            "OR a.id IN (SELECT f.asset_id FROM faces f JOIN persons pe ON pe.id=f.person_id WHERE pe.name LIKE ? AND f.rejected=0))")
        args += [like] * 8
    return " AND ".join(where), args


def get_asset_for(user: User, asset_id: int, allow_deleted: bool = False):
    a = db.q1("SELECT a.*, l.path AS lib_path, l.enabled AS lib_enabled FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?",
              (asset_id,))
    if a is None:
        raise HTTPException(404, "Photo not found.")
    if user.is_admin:
        if a["deleted_at"] is not None and not allow_deleted:
            raise HTTPException(404, "Photo is in the Recycle Bin.")
        return a
    ids = allowed_library_ids(user)
    if (ids is not None and a["library_id"] not in ids) or a["deleted_at"] is not None or a["hidden"] or not a["lib_enabled"]:
        raise HTTPException(404, "Photo not found.")
    return a


def asset_path(a) -> Path:
    return Path(a["lib_path"]) / a["path"]


MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".heic": "image/heic", ".heif": "image/heif", ".hif": "image/heif", ".gif": "image/gif", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".avif": "image/avif",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".3gp": "video/3gpp", ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo", ".webm": "video/webm", ".mts": "video/mp2t", ".m2ts": "video/mp2t", ".wmv": "video/x-ms-wmv",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
}


def cached_file(path: Path, media_type: str | None = None):
    if not path.exists():
        raise HTTPException(404, "Not ready yet.")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def ranged_file(request: Request, path: Path, media_type: str, filename: str | None = None, download: bool = False):
    """Serves a file with support for seeking (needed for video)."""
    try:
        st = path.stat()
    except OSError:
        raise HTTPException(404, "File is missing from the library.")
    size = st.st_size
    headers = {
        "Accept-Ranges": "bytes",
        "Last-Modified": formatdate(st.st_mtime, usegmt=True),
        "Cache-Control": "private, max-age=3600",
    }
    if filename:
        from urllib.parse import quote
        disp = "attachment" if download else "inline"
        headers["Content-Disposition"] = f"{disp}; filename*=UTF-8''{quote(filename)}"
    rng = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    if rng and rng.startswith("bytes="):
        try:
            s, e = rng[6:].split(",")[0].split("-")
            if s == "":
                start = max(0, size - int(e))
            else:
                start = int(s)
                end = int(e) if e else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        except ValueError:
            start, end = 0, size - 1
    length = end - start + 1
    headers["Content-Length"] = str(length)

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1024 * 512, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=media_type)
    return StreamingResponse(body(), status_code=status, headers=headers, media_type=media_type)


def ids_param(body: dict, key: str = "ids", limit: int = 100000) -> list[int]:
    ids = body.get(key) or []
    try:
        ids = [int(x) for x in ids][:limit]
    except (TypeError, ValueError):
        raise HTTPException(400, "Bad photo list.")
    return ids


def visible_ids(user: User, ids: list[int]) -> list[int]:
    """Filters a list of asset ids down to the ones this user may see."""
    if not ids:
        return []
    out = []
    lc, la = library_clause(user)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = db.q(f"SELECT a.id FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id IN ({','.join('?' * len(chunk))}) "
                    f"AND a.deleted_at IS NULL{lc}" + ("" if user.is_admin else " AND a.hidden=0 AND l.enabled=1"),
                    chunk + la)
        out += [r["id"] for r in rows]
    return out
