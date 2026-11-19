"""People and faces."""
import time

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image

from .. import cluster, db, media, xmp
from ..security import User, admin_user, current_user
from .common import cached_file, get_asset_for, library_clause

router = APIRouter(prefix="/api")


def _person_out(r):
    return {"id": r["id"], "name": r["name"], "count": r["face_count"], "cover": r["cover_face_id"],
            "hidden": bool(r["hidden"]), "unmatched": bool(r["unmatched"])}


@router.get("/people")
def people(user: User = Depends(current_user)):
    """Named people (hidden people only for the admin)."""
    lc, la = library_clause(user)
    if lc:
        rows = db.q(
            "SELECT p.*, (SELECT COUNT(DISTINCT f.asset_id) FROM faces f JOIN assets a ON a.id=f.asset_id "
            f"WHERE f.person_id=p.id AND f.rejected=0 AND a.deleted_at IS NULL AND a.hidden=0{lc}) AS vis "
            "FROM persons p WHERE p.name IS NOT NULL AND p.hidden=0 ORDER BY p.face_count DESC", la)
        rows = [r for r in rows if r["vis"]]
    else:
        hidden = "" if user.is_admin else " AND hidden=0"
        rows = db.q(f"SELECT * FROM persons WHERE name IS NOT NULL AND face_count>0{hidden} ORDER BY hidden, face_count DESC")
    return [_person_out(r) for r in rows]


@router.get("/people/{person_id}")
def person(person_id: int, user: User = Depends(current_user)):
    r = db.q1("SELECT * FROM persons WHERE id=?", (person_id,))
    if r is None or (not user.is_admin and (r["hidden"] or not r["name"])):
        raise HTTPException(404, "Person not found.")
    return _person_out(r)


def _rename(person_id: int, name: str) -> int:
    """Names a person. If someone already has that name, the two are merged."""
    name = name.strip()[:80]
    if not name:
        raise HTTPException(400, "Type a name.")
    other = db.q1("SELECT id FROM persons WHERE name=? AND id<>?", (name, person_id))
    if other:
        cluster.merge_persons(person_id, other["id"])
        target = other["id"]
    else:
        db.ex("UPDATE persons SET name=?, unmatched=0 WHERE id=?", (name, person_id))
        target = person_id
    db.ex("UPDATE faces SET confirmed=1 WHERE person_id=?", (target,))
    xmp.mark_dirty(r["asset_id"] for r in db.q("SELECT DISTINCT asset_id FROM faces WHERE person_id=?", (target,)))
    return target


@router.patch("/people/{person_id}")
def edit_person(person_id: int, body: dict, user: User = Depends(admin_user)):
    if db.q1("SELECT id FROM persons WHERE id=?", (person_id,)) is None:
        raise HTTPException(404, "Person not found.")
    target = person_id
    if "name" in body:
        if body["name"]:
            target = _rename(person_id, body["name"])
        else:
            db.ex("UPDATE persons SET name=NULL WHERE id=?", (person_id,))
    if "hidden" in body:
        db.ex("UPDATE persons SET hidden=? WHERE id=?", (1 if body["hidden"] else 0, target))
    if "unmatched" in body:
        db.ex("UPDATE persons SET unmatched=? WHERE id=?", (1 if body["unmatched"] else 0, target))
    if body.get("cover_face_id"):
        db.ex("UPDATE persons SET cover_face_id=? WHERE id=? AND EXISTS (SELECT 1 FROM faces WHERE id=? AND person_id=?)",
              (int(body["cover_face_id"]), target, int(body["cover_face_id"]), target))
    return {"id": target, "merged": target != person_id}


@router.post("/people/{person_id}/merge")
def merge(person_id: int, body: dict, user: User = Depends(admin_user)):
    into = int(body.get("into") or 0)
    if not into or db.q1("SELECT id FROM persons WHERE id=?", (into,)) is None:
        raise HTTPException(400, "Pick who to merge with.")
    cluster.merge_persons(person_id, into)
    xmp.mark_dirty(r["asset_id"] for r in db.q("SELECT DISTINCT asset_id FROM faces WHERE person_id=?", (into,)))
    return {"id": into}


@router.get("/people/{person_id}/faces")
def person_faces(person_id: int, offset: int = 0, limit: int = 200, user: User = Depends(admin_user)):
    rows = db.q("SELECT f.id, f.asset_id, f.score, f.manual, f.confirmed FROM faces f WHERE f.person_id=? AND f.rejected=0 "
                "ORDER BY f.confirmed DESC, f.score DESC LIMIT ? OFFSET ?", (person_id, limit, offset))
    return [dict(r) for r in rows]


@router.get("/admin/faces")
def face_groups(tab: str = "needs", offset: int = 0, limit: int = 60, sort: str = "count", user: User = Depends(admin_user)):
    """Groups for Library > Faces. tab: needs | named | unmatched | single"""
    from .. import settings
    min_faces = int(settings.get("cluster_min_faces"))
    where = {
        "needs": f"name IS NULL AND unmatched=0 AND hidden=0 AND face_count >= {min_faces}",
        "named": "name IS NOT NULL",
        "unmatched": "name IS NULL AND unmatched=1",
        "single": f"name IS NULL AND unmatched=0 AND hidden=0 AND face_count > 0 AND face_count < {min_faces}",
        "hidden": "hidden=1",
    }.get(tab)
    if where is None:
        raise HTTPException(400, "Unknown tab.")
    order = "created_at DESC" if sort == "new" else "face_count DESC, id"
    rows = db.q(f"SELECT * FROM persons WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset))
    counts = {
        "needs": db.val(f"SELECT COUNT(*) FROM persons WHERE name IS NULL AND unmatched=0 AND hidden=0 AND face_count >= {min_faces}", default=0),
        "named": db.val("SELECT COUNT(*) FROM persons WHERE name IS NOT NULL", default=0),
        "unmatched": db.val("SELECT COUNT(*) FROM persons WHERE name IS NULL AND unmatched=1", default=0),
        "single": db.val(f"SELECT COUNT(*) FROM persons WHERE name IS NULL AND unmatched=0 AND hidden=0 AND face_count > 0 AND face_count < {min_faces}", default=0),
        "hidden": db.val("SELECT COUNT(*) FROM persons WHERE hidden=1", default=0),
    }
    cache = cluster.named_cache() if tab in ("needs", "single", "unmatched") else None
    out = []
    for r in rows:
        samples = [x["id"] for x in db.q("SELECT id FROM faces WHERE person_id=? AND rejected=0 AND id<>? "
                                         "ORDER BY score DESC LIMIT 4", (r["id"], r["cover_face_id"] or 0))]
        years = db.q1("SELECT MIN(substr(a.taken_at,1,4)) AS y1, MAX(substr(a.taken_at,1,4)) AS y2, COUNT(DISTINCT a.id) AS photos "
                      "FROM faces f JOIN assets a ON a.id=f.asset_id WHERE f.person_id=? AND f.rejected=0", (r["id"],))
        item = _person_out(r)
        item.update(samples=samples, photos=years["photos"], years=(f"{years['y1']}" if years["y1"] == years["y2"]
                                                                    else f"{years['y1']} – {years['y2']}") if years["y1"] else "")
        if cache is not None:
            s = cluster.suggestion_for(r["centroid"], cache)
            if s:
                item["suggest"] = {"id": s[0], "name": s[1]}
        out.append(item)
    return {"items": out, "counts": counts}


@router.get("/faces/{face_id}/crop")
def face_crop(face_id: int, user: User = Depends(current_user)):
    f = db.q1("SELECT asset_id, x1, y1, x2, y2 FROM faces WHERE id=?", (face_id,))
    if f is None:
        raise HTTPException(404, "Face not found.")
    get_asset_for(user, f["asset_id"], allow_deleted=True)
    p = media.face_crop_path(face_id)
    if not p.exists():
        _make_crop(face_id, f)
    return cached_file(p, "image/webp")


def _make_crop(face_id, f):
    src = media.thumb_path(f["asset_id"], "p")
    if not src.exists():
        raise HTTPException(404, "Not ready yet.")
    with Image.open(src) as im:
        iw, ih = im.size
        x1, y1, x2, y2 = f["x1"] * iw, f["y1"] * ih, f["x2"] * iw, f["y2"] * ih
        side = max(x2 - x1, y2 - y1) * 1.35
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        box = (int(max(0, cx - side / 2)), int(max(0, cy - side / 2)), int(min(iw, cx + side / 2)), int(min(ih, cy + side / 2)))
        crop = im.convert("RGB").crop(box)
        crop.thumbnail((200, 200))
        dest = media.face_crop_path(face_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, "WEBP", quality=82)


@router.patch("/faces/{face_id}")
def edit_face(face_id: int, body: dict, user: User = Depends(admin_user)):
    """Move a face to someone, give it a new name, or say it's not a face."""
    f = db.q1("SELECT * FROM faces WHERE id=?", (face_id,))
    if f is None:
        raise HTTPException(404, "Face not found.")
    old = f["person_id"]
    new = None
    if body.get("reject"):
        db.ex("UPDATE faces SET rejected=1, person_id=NULL WHERE id=?", (face_id,))
    elif body.get("unassign"):
        db.ex("UPDATE faces SET person_id=NULL, confirmed=0 WHERE id=?", (face_id,))
    elif body.get("person_id"):
        new = int(body["person_id"])
        if db.q1("SELECT id FROM persons WHERE id=?", (new,)) is None:
            raise HTTPException(400, "Person not found.")
        db.ex("UPDATE faces SET person_id=?, confirmed=1 WHERE id=?", (new, face_id))
    elif body.get("name"):
        name = body["name"].strip()[:80]
        row = db.q1("SELECT id FROM persons WHERE name=?", (name,))
        new = row["id"] if row else db.ex("INSERT INTO persons(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid
        db.ex("UPDATE faces SET person_id=?, confirmed=1 WHERE id=?", (new, face_id))
    else:
        raise HTTPException(400, "Nothing to change.")
    for pid in {old, new} - {None}:
        cluster.recompute_person(pid)
    xmp.mark_dirty([f["asset_id"]])
    return {"ok": True, "person_id": new}


@router.post("/assets/{asset_id}/faces")
def add_face(asset_id: int, body: dict, user: User = Depends(admin_user)):
    """A face you drew by hand. Its fingerprint is worked out in the background."""
    get_asset_for(user, asset_id)
    try:
        x1, y1, x2, y2 = (max(0.0, min(1.0, float(body[k]))) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "Draw a box around the face first.")
    if x2 - x1 < 0.005 or y2 - y1 < 0.005:
        raise HTTPException(400, "That box is too small.")
    person_id = None
    name = (body.get("name") or "").strip()[:80]
    if body.get("person_id"):
        person_id = int(body["person_id"])
    elif name:
        row = db.q1("SELECT id FROM persons WHERE name=?", (name,))
        person_id = row["id"] if row else db.ex("INSERT INTO persons(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid
    fid = db.ex("INSERT INTO faces(asset_id, person_id, x1, y1, x2, y2, score, manual, confirmed, created_at) "
                "VALUES (?,?,?,?,?,?,1.0,1,1,?)", (asset_id, person_id, x1, y1, x2, y2, time.time())).lastrowid
    try:
        _make_crop(fid, {"asset_id": asset_id, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    except Exception:
        pass
    if person_id:
        cluster.recompute_person(person_id)
        db.ex("UPDATE persons SET face_count=(SELECT COUNT(*) FROM faces WHERE person_id=? AND rejected=0), "
              "cover_face_id=COALESCE(cover_face_id, ?) WHERE id=?", (person_id, fid, person_id))
    xmp.mark_dirty([asset_id])
    return {"id": fid, "person_id": person_id}
