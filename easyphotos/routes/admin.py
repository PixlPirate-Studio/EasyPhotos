"""Admin-only pages: users, libraries, jobs, settings, duplicates, recycle
bin, API keys, storage and backups."""
import hashlib
import json
import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .. import config, db, recycle, settings
from ..security import ROLES, User, admin_user
from .common import ids_param

router = APIRouter(prefix="/api/admin")


# ------------------------------------------------------------ users

def _user_out(r):
    libs = [x["library_id"] for x in db.q("SELECT library_id FROM user_libraries WHERE user_id=?", (r["id"],))]
    return {"id": r["id"], "username": r["username"], "role": r["role"], "has_password": bool(r["password_hash"]),
            "all_libraries": bool(r["all_libraries"]), "library_ids": libs,
            "created_at": r["created_at"], "last_login": r["last_login"]}


@router.get("/users")
def users(user: User = Depends(admin_user)):
    return [_user_out(r) for r in db.q("SELECT * FROM users ORDER BY role='admin' DESC, username")]


def _set_libraries(uid: int, body: dict):
    if "all_libraries" in body:
        db.ex("UPDATE users SET all_libraries=? WHERE id=?", (1 if body["all_libraries"] else 0, uid))
    if "library_ids" in body:
        ids = [int(x) for x in body.get("library_ids") or []]
        with db.tx() as c:
            c.execute("DELETE FROM user_libraries WHERE user_id=?", (uid,))
            c.executemany("INSERT OR IGNORE INTO user_libraries(user_id, library_id) VALUES (?,?)", [(uid, i) for i in ids])


@router.post("/users")
def create_user(body: dict, user: User = Depends(admin_user)):
    """Creates a user with no password. They choose it the first time they sign in."""
    username = (body.get("username") or "").strip()
    role = body.get("role") or "viewer"
    if role not in ROLES:
        raise HTTPException(400, "Role must be Viewer, User or Admin.")
    if not (2 <= len(username) <= 40) or not all(c.isalnum() or c in "._-" for c in username):
        raise HTTPException(400, "Usernames can use letters, numbers, dots, dashes and underscores (2-40 characters).")
    if db.q1("SELECT id FROM users WHERE username=?", (username,)):
        raise HTTPException(400, "That username is already taken.")
    uid = db.ex("INSERT INTO users(username, role, password_hash, all_libraries, created_at) VALUES (?,?,NULL,1,?)",
                (username, role, time.time())).lastrowid
    _set_libraries(uid, body)
    return _user_out(db.q1("SELECT * FROM users WHERE id=?", (uid,)))


@router.patch("/users/{uid}")
def edit_user(uid: int, body: dict, user: User = Depends(admin_user)):
    target = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if target is None:
        raise HTTPException(404, "User not found.")
    if "role" in body:
        if body["role"] not in ROLES:
            raise HTTPException(400, "Role must be Viewer, User or Admin.")
        if target["role"] == "admin" and body["role"] != "admin":
            if db.val("SELECT COUNT(*) FROM users WHERE role='admin'", default=0) <= 1:
                raise HTTPException(400, "You need at least one admin.")
        db.ex("UPDATE users SET role=? WHERE id=?", (body["role"], uid))
    _set_libraries(uid, body)
    return _user_out(db.q1("SELECT * FROM users WHERE id=?", (uid,)))


@router.post("/users/{uid}/reset-password")
def reset_password(uid: int, user: User = Depends(admin_user)):
    """Clears the password and signs them out. They pick a new one next sign-in."""
    if db.q1("SELECT id FROM users WHERE id=?", (uid,)) is None:
        raise HTTPException(404, "User not found.")
    db.ex("UPDATE users SET password_hash=NULL WHERE id=?", (uid,))
    db.ex("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


@router.delete("/users/{uid}")
def delete_user(uid: int, user: User = Depends(admin_user)):
    if uid == user.id:
        raise HTTPException(400, "You can't delete your own account.")
    target = db.q1("SELECT role FROM users WHERE id=?", (uid,))
    if target is None:
        raise HTTPException(404, "User not found.")
    if target["role"] == "admin" and db.val("SELECT COUNT(*) FROM users WHERE role='admin'", default=0) <= 1:
        raise HTTPException(400, "You need at least one admin.")
    db.ex("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


# ------------------------------------------------------------ libraries

def _lib_out(r):
    stats = db.q1("SELECT COUNT(*) AS n, SUM(CASE WHEN meta_status=0 THEN 1 ELSE 0 END) AS pending, "
                  "SUM(CASE WHEN meta_status=-1 THEN 1 ELSE 0 END) AS errors, "
                  "SUM(CASE WHEN face_status IN (1,2) THEN 1 ELSE 0 END) AS faces_done "
                  "FROM assets WHERE library_id=? AND deleted_at IS NULL", (r["id"],))
    faces = db.val("SELECT COUNT(*) FROM faces f JOIN assets a ON a.id=f.asset_id WHERE a.library_id=? AND f.rejected=0", (r["id"],), 0)
    p = Path(r["path"])
    writable = os.access(p, os.W_OK) if p.exists() else False
    return {"id": r["id"], "name": r["name"], "path": r["path"], "enabled": bool(r["enabled"]),
            "exists": p.is_dir(), "writable": writable, "last_scan_at": r["last_scan_at"],
            "count": stats["n"] or 0, "pending": stats["pending"] or 0, "errors": stats["errors"] or 0,
            "faces_done": stats["faces_done"] or 0, "faces": faces}


@router.get("/libraries")
def libraries(user: User = Depends(admin_user)):
    return [_lib_out(r) for r in db.q("SELECT * FROM libraries ORDER BY name")]


@router.post("/libraries")
def add_library(body: dict, user: User = Depends(admin_user)):
    name = (body.get("name") or "").strip()[:80]
    path = (body.get("path") or "").strip().rstrip("/") or "/"
    if not name:
        raise HTTPException(400, "Give the library a name.")
    if not path.startswith("/") or not Path(path).is_dir():
        raise HTTPException(400, f"Folder {path} was not found inside the container. Map it in the Unraid container settings first.")
    real = os.path.realpath(path)
    for r in db.q("SELECT path FROM libraries"):
        other = os.path.realpath(r["path"])
        if real == other or real.startswith(other + "/") or other.startswith(real + "/"):
            raise HTTPException(400, f"That folder overlaps with a library you already added ({r['path']}).")
    lid = db.ex("INSERT INTO libraries(name, path, created_at) VALUES (?,?,?)", (name, path, time.time())).lastrowid
    db.ex("INSERT INTO job_commands(name, args, created_at) VALUES ('scan', ?, ?)", (json.dumps({"library_id": lid}), time.time()))
    return _lib_out(db.q1("SELECT * FROM libraries WHERE id=?", (lid,)))


@router.patch("/libraries/{lid}")
def edit_library(lid: int, body: dict, user: User = Depends(admin_user)):
    if "name" in body and body["name"].strip():
        db.ex("UPDATE libraries SET name=? WHERE id=?", (body["name"].strip()[:80], lid))
    if "enabled" in body:
        db.ex("UPDATE libraries SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, lid))
    return _lib_out(db.q1("SELECT * FROM libraries WHERE id=?", (lid,)))


@router.delete("/libraries/{lid}")
def remove_library(lid: int, user: User = Depends(admin_user)):
    """Removes the library from EasyPhotos. Your files are not touched."""
    from .. import media
    for r in db.q("SELECT id FROM assets WHERE library_id=?", (lid,)):
        media.remove_derivatives(r["id"])
    persons = [r["person_id"] for r in db.q(
        "SELECT DISTINCT f.person_id FROM faces f JOIN assets a ON a.id=f.asset_id WHERE a.library_id=? AND f.person_id IS NOT NULL", (lid,))]
    db.ex("DELETE FROM libraries WHERE id=?", (lid,))
    from ..cluster import recompute_person
    for pid in persons:
        recompute_person(pid)
    return {"ok": True}


@router.get("/libraries/{lid}/folders")
def library_folders(lid: int, user: User = Depends(admin_user)):
    """Every folder in a library with photo counts (including sub-folders)."""
    rows = db.q("SELECT folder, COUNT(*) AS n, SUM(CASE WHEN face_status IN (1,2) THEN 1 ELSE 0 END) AS fd, "
                "SUM(CASE WHEN meta_status=0 THEN 1 ELSE 0 END) AS pend "
                "FROM assets WHERE library_id=? AND deleted_at IS NULL GROUP BY folder", (lid,))
    faces = {r["folder"]: r["n"] for r in db.q(
        "SELECT a.folder, COUNT(*) AS n FROM faces f JOIN assets a ON a.id=f.asset_id WHERE a.library_id=? AND f.rejected=0 "
        "GROUP BY a.folder", (lid,))}
    tree = {}
    for r in rows:
        parts = r["folder"].split("/") if r["folder"] else []
        for i in range(0, len(parts) + 1):
            key = "/".join(parts[:i])
            node = tree.setdefault(key, {"path": key, "count": 0, "faces_done": 0, "pending": 0, "faces": 0})
            node["count"] += r["n"]
            node["faces_done"] += r["fd"] or 0
            node["pending"] += r["pend"] or 0
            node["faces"] += faces.get(r["folder"], 0)
    return sorted(tree.values(), key=lambda x: x["path"].lower())


@router.get("/browse")
def browse(path: str = "", user: User = Depends(admin_user)):
    """Lists folders inside the container so you can pick a library folder."""
    base = Path(path or str(config.LIBRARIES_ROOT))
    if not base.is_absolute():
        raise HTTPException(400, "Use a full path.")
    if not base.is_dir():
        if path:
            raise HTTPException(404, "Folder not found.")
        base = Path("/")
    dirs = []
    try:
        for e in sorted(os.scandir(base), key=lambda e: e.name.lower()):
            if e.is_dir(follow_symlinks=True) and not e.name.startswith("."):
                dirs.append(e.name)
    except OSError as e:
        raise HTTPException(400, f"Can't open that folder: {e}")
    parent = str(base.parent) if str(base) != "/" else None
    return {"path": str(base), "parent": parent, "dirs": dirs[:500], "root": str(config.LIBRARIES_ROOT)}


# ------------------------------------------------------------ jobs

JOB_NAMES = {"scan", "find_faces", "rescan_faces", "recluster", "rebuild_thumbs", "duplicates", "refresh_places"}


@router.get("/jobs")
def jobs(user: User = Depends(admin_user)):
    st = {r["name"]: dict(r) for r in db.q("SELECT * FROM job_status")}
    c = db.q1(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN meta_status=0 THEN 1 ELSE 0 END) AS meta_pending, "
        "SUM(CASE WHEN meta_status=-1 THEN 1 ELSE 0 END) AS meta_errors, "
        "SUM(CASE WHEN kind='image' AND is_live_part=0 THEN 1 ELSE 0 END) AS images, "
        "SUM(CASE WHEN face_status=0 THEN 1 ELSE 0 END) AS faces_pending, "
        "SUM(CASE WHEN face_status IN (1,2,-1) THEN 1 ELSE 0 END) AS faces_done "
        "FROM assets WHERE deleted_at IS NULL")
    s = settings.get_all()
    face_rate = (st.get("faces") or {}).get("rate")
    eta = None
    if face_rate and c["faces_pending"]:
        eta = c["faces_pending"] / face_rate * 60
    worker = st.get("worker") or {}
    alive = worker.get("updated_at") and time.time() - worker["updated_at"] < 120
    return {
        "status": st, "counts": {k: c[k] or 0 for k in c.keys()},
        "faces_started": s["faces_started"], "faces_enabled": s["faces_enabled"], "paused": s["paused"],
        "faces_found": db.val("SELECT COUNT(*) FROM faces WHERE rejected=0", default=0),
        "people_groups": db.val("SELECT COUNT(*) FROM persons WHERE face_count>0", default=0),
        "faces_eta_seconds": eta, "worker_alive": bool(alive),
        "threads": min(s["worker_threads"], os.cpu_count() or 1), "night_threads": min(s["night_threads"], os.cpu_count() or 1),
        "night_boost": s["night_boost"], "cpus": os.cpu_count(),
    }


@router.post("/jobs/{name}")
def run_job(name: str, body: dict | None = None, user: User = Depends(admin_user)):
    if name == "pause":
        settings.set_many({"paused": True})
        return {"ok": True}
    if name == "resume":
        settings.set_many({"paused": False})
        return {"ok": True}
    if name == "stop_faces":
        settings.set_many({"faces_started": False})
        return {"ok": True}
    if name not in JOB_NAMES:
        raise HTTPException(400, "Unknown job.")
    if name == "find_faces":
        settings.set_many({"faces_started": True, "faces_enabled": True})
    db.ex("INSERT INTO job_commands(name, args, created_at) VALUES (?,?,?)", (name, json.dumps(body or {}), time.time()))
    return {"ok": True}


# ------------------------------------------------------------ settings

@router.get("/settings")
def get_settings(user: User = Depends(admin_user)):
    s = settings.get_all()
    s["_threads_available"] = os.cpu_count()
    s["_thumbs_dir_effective"] = str(settings.thumbs_dir())
    s["_faces_dir_effective"] = str(settings.faces_dir())
    return s


@router.put("/settings")
def put_settings(body: dict, user: User = Depends(admin_user)):
    body = {k: v for k, v in body.items() if not k.startswith("_")}
    before = settings.get_all()
    try:
        for k in ("thumbs_dir", "faces_dir"):
            if body.get(k):
                p = Path(body[k])
                p.mkdir(parents=True, exist_ok=True)
                if not os.access(p, os.W_OK):
                    raise ValueError(f"EasyPhotos can't write to {p}. Check the folder mapping in Unraid.")
        changed = settings.set_many(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(400, f"Can't use that folder: {e}")
    now = time.time()
    if any(k in changed and changed[k] != before[k] for k in ("thumb_size", "preview_size", "thumb_format", "thumb_quality", "thumbs_dir")):
        db.ex("INSERT INTO job_commands(name, args, created_at) VALUES ('rebuild_thumbs', '{}', ?)", (now,))
    if "face_strictness" in changed and changed["face_strictness"] != before["face_strictness"]:
        db.ex("INSERT INTO job_commands(name, args, created_at) VALUES ('recluster', '{}', ?)", (now,))
    if "places_enabled" in changed and changed["places_enabled"] and not before["places_enabled"]:
        db.ex("INSERT INTO job_commands(name, args, created_at) VALUES ('refresh_places', '{}', ?)", (now,))
    if "xmp_enabled" in changed and changed["xmp_enabled"] and not before["xmp_enabled"]:
        db.ex("UPDATE assets SET xmp_dirty=1 WHERE id IN (SELECT asset_id FROM asset_tags) OR description IS NOT NULL "
              "OR id IN (SELECT f.asset_id FROM faces f JOIN persons p ON p.id=f.person_id WHERE p.name IS NOT NULL)")
    return get_settings(user)


# ------------------------------------------------------------ duplicates

@router.get("/duplicates")
def duplicates(kind: str = "exact", status: str = "open", offset: int = 0, limit: int = 20, user: User = Depends(admin_user)):
    counts = {f"{r['kind']}:{r['status']}": r["n"] for r in db.q("SELECT kind, status, COUNT(*) AS n FROM dup_groups GROUP BY kind, status")}
    if status == "kept":
        groups = db.q("SELECT * FROM dup_groups WHERE status='kept' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    else:
        groups = db.q("SELECT * FROM dup_groups WHERE kind=? AND status='open' ORDER BY id LIMIT ? OFFSET ?", (kind, limit, offset))
    out = []
    for g in groups:
        members = db.q(
            "SELECT a.id, a.filename, a.path, a.size, a.width, a.height, a.taken_at, a.thumb_ver, a.lat, a.kind, a.deleted_at, "
            "l.name AS library, (SELECT COUNT(*) FROM faces f JOIN persons p ON p.id=f.person_id WHERE f.asset_id=a.id AND p.name IS NOT NULL) AS named_faces, "
            "(SELECT COUNT(*) FROM asset_tags t WHERE t.asset_id=a.id) AS tags, "
            "(SELECT COUNT(*) FROM album_assets x WHERE x.asset_id=a.id) AS albums "
            "FROM dup_members m JOIN assets a ON a.id=m.asset_id JOIN libraries l ON l.id=a.library_id "
            "WHERE m.group_id=? AND a.deleted_at IS NULL", (g["id"],))
        members = [dict(m) for m in members]
        if len(members) < 2 and status != "kept":
            continue
        # Suggest keeping the copy with the most info and the most pixels.
        best = max(members, key=lambda m: ((m["named_faces"] or 0) * 4 + (m["tags"] or 0) * 2 + (m["albums"] or 0) * 2 + (1 if m["lat"] else 0),
                                           (m["width"] or 0) * (m["height"] or 0), m["size"], -m["id"])) if members else None
        out.append({"id": g["id"], "kind": g["kind"], "status": g["status"], "members": members,
                    "suggest_keep": best["id"] if best else None})
    return {"groups": out, "counts": counts}


@router.post("/duplicates/{gid}")
def resolve_duplicate(gid: int, body: dict, user: User = Depends(admin_user)):
    g = db.q1("SELECT * FROM dup_groups WHERE id=?", (gid,))
    if g is None:
        raise HTTPException(404, "Group not found.")
    action = body.get("action")
    members = [r["asset_id"] for r in db.q("SELECT asset_id FROM dup_members WHERE group_id=?", (gid,))]
    keep = int(body.get("keep") or 0)
    if action in ("hide", "delete") and keep not in members:
        raise HTTPException(400, "Choose which copy to keep.")
    others = [m for m in members if m != keep]
    if action == "hide":
        db.exmany("UPDATE assets SET hidden=1 WHERE id=?", [(a,) for a in others])
        db.ex("UPDATE dup_groups SET status='resolved' WHERE id=?", (gid,))
        return {"ok": True}
    if action == "delete":
        if body.get("confirm") != "DELETE":
            raise HTTPException(400, "Type DELETE to confirm.")
        if not settings.get("allow_delete"):
            raise HTTPException(403, "Deleting is turned off in Admin settings.")
        res = recycle.delete_assets(others)
        if res["moved"]:
            db.ex("UPDATE dup_groups SET status='resolved' WHERE id=?", (gid,))
        return {"ok": not res["errors"], **res}
    if action == "keep_both":
        db.ex("UPDATE dup_groups SET status='kept' WHERE id=?", (gid,))
        return {"ok": True}
    if action == "reopen":
        db.ex("DELETE FROM dup_groups WHERE id=?", (gid,))
        db.ex("INSERT INTO job_commands(name, args, created_at) VALUES ('duplicates', '{}', ?)", (time.time(),))
        return {"ok": True}
    raise HTTPException(400, "Unknown action.")


# ------------------------------------------------------------ recycle bin

@router.get("/recycle")
def recycle_list(offset: int = 0, limit: int = 200, user: User = Depends(admin_user)):
    rows = db.q("SELECT a.id, a.filename, a.path, a.folder, a.size, a.deleted_at, a.thumb_ver, a.kind, l.name AS library, l.path AS lib_path "
                "FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.deleted_at IS NOT NULL AND a.is_live_part=0 "
                "ORDER BY a.deleted_at DESC LIMIT ? OFFSET ?", (limit, offset))
    total = db.q1("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM assets WHERE deleted_at IS NOT NULL")
    return {"items": [dict(r) for r in rows], "count": total["n"], "bytes": total["bytes"]}


@router.post("/recycle/restore")
def recycle_restore(body: dict, user: User = Depends(admin_user)):
    return recycle.restore_assets(ids_param(body))


@router.post("/recycle/purge")
def recycle_purge(body: dict, user: User = Depends(admin_user)):
    if body.get("confirm") != "DELETE":
        raise HTTPException(400, "Type DELETE to confirm.")
    return recycle.purge_assets(ids_param(body))


@router.post("/recycle/empty")
def recycle_empty(body: dict, user: User = Depends(admin_user)):
    if body.get("confirm") != "EMPTY":
        raise HTTPException(400, "Type EMPTY to confirm.")
    ids = [r["id"] for r in db.q("SELECT id FROM assets WHERE deleted_at IS NOT NULL")]
    return recycle.purge_assets(ids)


# ------------------------------------------------------------ API keys

def _key_out(r):
    folders = json.loads(r["folders"] or "[]")
    libs = {x["id"]: x["name"] for x in db.q("SELECT id, name FROM libraries")}
    labels = []
    for f in folders:
        lib = libs.get(f.get("library_id"), "?")
        labels.append(f"{lib}/{f['folder']}" if f.get("folder") else lib)
    return {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "folders": folders, "folder_labels": labels,
            "include_videos": bool(r["include_videos"]), "created_at": r["created_at"], "last_used": r["last_used"]}


def _clean_folders(folders):
    out = []
    for f in folders or []:
        try:
            lid = int(f["library_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if db.q1("SELECT id FROM libraries WHERE id=?", (lid,)) is None:
            continue
        out.append({"library_id": lid, "folder": str(f.get("folder") or "").strip("/")})
    return out


@router.get("/apikeys")
def apikeys(user: User = Depends(admin_user)):
    return [_key_out(r) for r in db.q("SELECT * FROM api_keys ORDER BY created_at DESC")]


@router.post("/apikeys")
def create_apikey(body: dict, user: User = Depends(admin_user)):
    name = (body.get("name") or "").strip()[:60] or "API key"
    folders = _clean_folders(body.get("folders"))
    key = "ep_" + secrets.token_urlsafe(30)
    kid = db.ex("INSERT INTO api_keys(name, key_hash, prefix, folders, include_videos, created_at) VALUES (?,?,?,?,?,?)",
                (name, hashlib.sha256(key.encode()).hexdigest(), key[:10], json.dumps(folders),
                 1 if body.get("include_videos") else 0, time.time())).lastrowid
    out = _key_out(db.q1("SELECT * FROM api_keys WHERE id=?", (kid,)))
    out["key"] = key  # shown once
    return out


@router.patch("/apikeys/{kid}")
def edit_apikey(kid: int, body: dict, user: User = Depends(admin_user)):
    if "name" in body:
        db.ex("UPDATE api_keys SET name=? WHERE id=?", ((body["name"] or "API key").strip()[:60], kid))
    if "folders" in body:
        db.ex("UPDATE api_keys SET folders=? WHERE id=?", (json.dumps(_clean_folders(body["folders"])), kid))
    if "include_videos" in body:
        db.ex("UPDATE api_keys SET include_videos=? WHERE id=?", (1 if body["include_videos"] else 0, kid))
    return _key_out(db.q1("SELECT * FROM api_keys WHERE id=?", (kid,)))


@router.delete("/apikeys/{kid}")
def delete_apikey(kid: int, user: User = Depends(admin_user)):
    db.ex("DELETE FROM api_keys WHERE id=?", (kid,))
    return {"ok": True}


# ------------------------------------------------------------ logs

def _tail(path: Path, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        data = f.read().decode("utf-8", errors="replace")
    return data.splitlines()[-max_lines:]


@router.get("/logs")
def logs(which: str = "worker", lines: int = 400, problems: int = 0, user: User = Depends(admin_user)):
    """The newest lines of the background worker or web log."""
    name = "web.log" if which == "web" else "worker.log"
    out = _tail(config.LOG_DIR / name, max(10, min(lines, 3000)) * (5 if problems else 1))
    if problems:
        out = [ln for ln in out if " WARNING " in ln or " ERROR " in ln or "Traceback" in ln or "Error" in ln]
    out = out[-max(10, min(lines, 3000)):]
    return {"file": str(config.LOG_DIR / name), "lines": out}


# ------------------------------------------------------------ storage & backup

def _dir_size(p: Path, limit_seconds: float = 5.0) -> int | None:
    total, t0 = 0, time.time()
    try:
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
            if time.time() - t0 > limit_seconds:
                return None
    except OSError:
        return None
    return total


@router.get("/storage")
def storage(user: User = Depends(admin_user)):
    out = {}
    for key, p in (("thumbs", settings.thumbs_dir()), ("faces", settings.faces_dir()), ("config", config.CONFIG_DIR)):
        info = {"path": str(p), "exists": p.exists()}
        if p.exists():
            du = shutil.disk_usage(p)
            info.update(free=du.free, total=du.total, writable=os.access(p, os.W_OK))
        out[key] = info
    out["thumbs"]["used"] = _dir_size(settings.thumbs_dir())
    out["db_size"] = sum(os.path.getsize(f) for f in (config.DB_PATH, Path(str(config.DB_PATH) + "-wal")) if os.path.exists(f))
    out["encrypted"] = bool(config.DB_KEY) and db._sqlcipher is not None
    out["backups"] = sorted((f.name for f in config.BACKUP_DIR.glob("easyphotos-*.db")), reverse=True)[:10]
    return out


@router.post("/backup")
def backup(user: User = Depends(admin_user)):
    db.checkpoint()
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("easyphotos-%Y%m%d-%H%M%S.db")
    shutil.copy2(config.DB_PATH, config.BACKUP_DIR / name)
    old = sorted(config.BACKUP_DIR.glob("easyphotos-*.db"), reverse=True)[10:]
    for f in old:
        f.unlink(missing_ok=True)
    return {"ok": True, "file": name}
