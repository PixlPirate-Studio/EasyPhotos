"""People and faces."""
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image

from .. import cluster, db, media, xmp
from ..security import User, admin_user, audit, current_user
from .common import cached_file, get_asset_for, library_clause

router = APIRouter(prefix="/api")


def _person_out(r):
    return {"id": r["id"], "name": r["name"], "count": r["face_count"], "cover": r["cover_face_id"],
            "hidden": bool(r["hidden"]), "unmatched": bool(r["unmatched"]), "favorite": bool(r["favorite"])}


@router.get("/people")
def people(user: User = Depends(current_user)):
    """Named people (hidden people only for the admin)."""
    lc, la = library_clause(user)
    if lc:
        rows = db.q(
            "SELECT p.*, (SELECT COUNT(DISTINCT f.asset_id) FROM faces f JOIN assets a ON a.id=f.asset_id "
            f"WHERE f.person_id=p.id AND f.rejected=0 AND a.deleted_at IS NULL AND a.hidden=0{lc}) AS vis "
            "FROM persons p WHERE p.name IS NOT NULL AND p.hidden=0 ORDER BY p.favorite DESC, p.face_count DESC", la)
        rows = [r for r in rows if r["vis"]]
    else:
        hidden = "" if user.is_admin else " AND hidden=0"
        rows = db.q(f"SELECT * FROM persons WHERE name IS NOT NULL AND face_count>0{hidden} ORDER BY hidden, favorite DESC, face_count DESC")
    return [_person_out(r) for r in rows]


@router.get("/people/{person_id}")
def person(person_id: int, user: User = Depends(current_user)):
    r = db.q1("SELECT * FROM persons WHERE id=?", (person_id,))
    if r is None or (not user.is_admin and (r["hidden"] or not r["name"])):
        raise HTTPException(404, "Person not found.")
    return _person_out(r)


class SameName(Exception):
    """Someone already has this name: the admin must say if it's the same person."""

    def __init__(self, name, choices):
        self.name, self.choices = name, choices


def _same_name(name: str, exclude_id: int | None = None):
    rows = db.q("SELECT id, name, face_count, cover_face_id FROM persons WHERE name=? AND id<>? ORDER BY face_count DESC",
                (name, exclude_id or 0))
    return [{"id": r["id"], "name": r["name"], "count": r["face_count"], "cover": r["cover_face_id"]} for r in rows]


def same_name_response(e: SameName):
    return JSONResponse(status_code=409, content={
        "error": f"There is already someone named {e.name}. Is this the same person?",
        "name": e.name, "choices": e.choices})


def _target_for_name(name: str, body: dict, exclude_id: int | None = None) -> int | None:
    """Who a typed name refers to. Returns a person id, or None = make a new person.
    body may say {"person_id": id} (same person) or {"separate": true} (different person)."""
    if body.get("person_id"):
        return int(body["person_id"])
    if body.get("merge_into"):
        return int(body["merge_into"])
    if body.get("separate"):
        return None
    choices = _same_name(name, exclude_id)
    if choices:
        raise SameName(name, choices)
    return None


def _rename(person_id: int, name: str, body: dict | None = None) -> int:
    """Names a person. If someone already has that name, asks whether they are the
    same person (merge) or a different person who happens to share the name."""
    name = name.strip()[:80]
    if not name:
        raise HTTPException(400, "Type a name.")
    target_existing = _target_for_name(name, body or {}, exclude_id=person_id)
    if target_existing and target_existing != person_id:
        cluster.merge_persons(person_id, target_existing)
        target = target_existing
    else:
        db.ex("UPDATE persons SET name=?, unmatched=0 WHERE id=?", (name, person_id))
        target = person_id
    db.ex("UPDATE faces SET confirmed=1 WHERE person_id=?", (target,))
    # if they once said "Not X" but now say it IS X, the newer answer wins
    db.ex("DELETE FROM face_not WHERE person_id=? AND face_id IN (SELECT id FROM faces WHERE person_id=?)", (target, target))
    xmp.mark_dirty(r["asset_id"] for r in db.q("SELECT DISTINCT asset_id FROM faces WHERE person_id=?", (target,)))
    return target


@router.patch("/people/{person_id}")
def edit_person(person_id: int, body: dict, user: User = Depends(admin_user)):
    if db.q1("SELECT id FROM persons WHERE id=?", (person_id,)) is None:
        raise HTTPException(404, "Person not found.")
    target = person_id
    if "name" in body:
        if body["name"]:
            try:
                target = _rename(person_id, body["name"], body)
            except SameName as e:
                return same_name_response(e)
            audit(None, user, "named a person %s%s", body["name"].strip(), " (merged)" if target != person_id else "")
        else:
            # Remove the name: back to an unnamed group the AI can merge or split again.
            db.ex("UPDATE persons SET name=NULL, unmatched=0 WHERE id=?", (person_id,))
            db.ex("UPDATE faces SET confirmed=0 WHERE person_id=?", (person_id,))
            xmp.mark_dirty(r["asset_id"] for r in db.q("SELECT DISTINCT asset_id FROM faces WHERE person_id=?", (person_id,)))
            audit(None, user, "removed the name from person %d", person_id)
    if body.get("not_person"):
        other = int(body["not_person"])
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO face_not(face_id, person_id) SELECT id, ? FROM faces WHERE person_id=? AND rejected=0",
                      (other, person_id))
        who = db.val("SELECT name FROM persons WHERE id=?", (other,), default="that person")
        audit(None, user, "marked group %d as not %s", person_id, who)
        r = db.q1("SELECT centroid FROM persons WHERE id=?", (person_id,))
        s = cluster.suggestion_for(r["centroid"], cluster.named_cache(), cluster.not_people([person_id])[person_id])
        return {"id": person_id, "merged": False, "suggest": {"id": s[0], "name": s[1], "match": _match_word(s[2])} if s else None}
    if "favorite" in body:
        db.ex("UPDATE persons SET favorite=? WHERE id=?", (1 if body["favorite"] else 0, target))
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


def _match_word(sim: float) -> str:
    from .. import settings
    thr = settings.similarity_threshold()
    return "strong" if sim >= thr - 0.04 else "good" if sim >= thr - 0.09 else "maybe"


def _possible(min_faces: int):
    """Unnamed groups that look like someone already named: [(sim, person_row, suggestion)], best first."""
    cache = cluster.named_cache()
    if not cache["ids"]:
        return [], cache
    rows = db.q("SELECT * FROM persons WHERE name IS NULL AND unmatched=0 AND hidden=0 AND face_count > 0 AND centroid IS NOT NULL")
    if not rows:
        return [], cache
    nots = cluster.not_people(r["id"] for r in rows)
    out = []
    for r in rows:
        s = cluster.suggestion_for(r["centroid"], cache, nots.get(r["id"]))
        if s:
            out.append((s[2], r, s))
    out.sort(key=lambda x: (-x[0], -x[1]["face_count"]))
    return out, cache


@router.get("/admin/faces")
def face_groups(tab: str = "needs", offset: int = 0, limit: int = 60, sort: str = "count", user: User = Depends(admin_user)):
    """Groups for Library > Faces. tab: needs | possible | named | unmatched | single | hidden"""
    from .. import settings
    min_faces = int(settings.get("cluster_min_faces"))
    limit = max(1, min(limit, 400))
    where = {
        "needs": f"name IS NULL AND unmatched=0 AND hidden=0 AND face_count >= {min_faces}",
        "possible": None,
        "named": "name IS NOT NULL",
        "unmatched": "name IS NULL AND unmatched=1",
        "single": f"name IS NULL AND unmatched=0 AND hidden=0 AND face_count > 0 AND face_count < {min_faces}",
        "hidden": "hidden=1",
    }
    if tab not in where:
        raise HTTPException(400, "Unknown tab.")
    possible, cache = _possible(min_faces)
    fav_only = cluster.favorites_only()
    hidden_by_fav = 0
    if tab == "possible":
        page = possible[offset:offset + limit]
        rows = [x[1] for x in page]
        sugg = {x[1]["id"]: x[2] for x in page}
    elif fav_only and tab in ("needs", "single"):
        # only groups that look like a favorite person
        keep = [x for x in possible if (x[1]["face_count"] >= min_faces) == (tab == "needs")]
        if sort == "new":
            keep.sort(key=lambda x: -(x[1]["created_at"] or 0))
        else:
            keep.sort(key=lambda x: (-x[1]["face_count"], x[1]["id"]))
        page = keep[offset:offset + limit]
        rows = [x[1] for x in page]
        sugg = {x[1]["id"]: x[2] for x in page}
    else:
        order = "created_at DESC" if sort == "new" else "face_count DESC, id"
        if tab == "named":
            order = "favorite DESC, " + order
        rows = db.q(f"SELECT * FROM persons WHERE {where[tab]} ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset))
        sugg = {}
        if tab in ("needs", "single", "unmatched") and cache["ids"]:
            nots = cluster.not_people(r["id"] for r in rows)
            for r in rows:
                s = cluster.suggestion_for(r["centroid"], cache, nots.get(r["id"]))
                if s:
                    sugg[r["id"]] = s
    if fav_only:
        all_unnamed = db.val(f"SELECT COUNT(*) FROM persons WHERE ({where['needs']}) OR ({where['single']})", default=0)
        hidden_by_fav = max(0, all_unnamed - len(possible))
    counts = {
        "needs": sum(1 for x in possible if x[1]["face_count"] >= min_faces) if fav_only else
        db.val(f"SELECT COUNT(*) FROM persons WHERE {where['needs']}", default=0),
        "possible": len(possible),
        "named": db.val("SELECT COUNT(*) FROM persons WHERE name IS NOT NULL", default=0),
        "unmatched": db.val("SELECT COUNT(*) FROM persons WHERE name IS NULL AND unmatched=1", default=0),
        "single": sum(1 for x in possible if x[1]["face_count"] < min_faces) if fav_only else
        db.val(f"SELECT COUNT(*) FROM persons WHERE {where['single']}", default=0),
        "hidden": db.val("SELECT COUNT(*) FROM persons WHERE hidden=1", default=0),
    }
    out = []
    for r in rows:
        samples = [x["id"] for x in db.q("SELECT id FROM faces WHERE person_id=? AND rejected=0 AND id<>? "
                                         "ORDER BY score DESC LIMIT 4", (r["id"], r["cover_face_id"] or 0))]
        years = db.q1("SELECT MIN(substr(a.taken_at,1,4)) AS y1, MAX(substr(a.taken_at,1,4)) AS y2, COUNT(DISTINCT a.id) AS photos "
                      "FROM faces f JOIN assets a ON a.id=f.asset_id WHERE f.person_id=? AND f.rejected=0", (r["id"],))
        item = _person_out(r)
        item.update(samples=samples, photos=years["photos"], years=(f"{years['y1']}" if years["y1"] == years["y2"]
                                                                    else f"{years['y1']} – {years['y2']}") if years["y1"] else "")
        s = sugg.get(r["id"])
        if s:
            item["suggest"] = {"id": s[0], "name": s[1], "match": _match_word(s[2])}
        out.append(item)
    favorites = db.val("SELECT COUNT(*) FROM persons WHERE favorite=1 AND name IS NOT NULL", default=0)
    return {"items": out, "counts": counts, "favorites_only": fav_only, "favorites": favorites, "hidden_by_favorites": hidden_by_fav}


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
        try:
            new = _target_for_name(name, body)
        except SameName as e:
            return same_name_response(e)
        if new is None:
            new = db.ex("INSERT INTO persons(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid
        db.ex("UPDATE faces SET person_id=?, confirmed=1 WHERE id=?", (new, face_id))
    else:
        raise HTTPException(400, "Nothing to change.")
    for pid in {old, new} - {None}:
        cluster.recompute_person(pid)
    xmp.mark_dirty([f["asset_id"]])
    return {"ok": True, "person_id": new}


@router.post("/people/{person_id}/ignore")
def ignore_person(person_id: int, user: User = Depends(admin_user)):
    """Trash can on a Faces card: stop asking about everyone in this group.
    The faces are set aside (not deleted), the photos are untouched."""
    r = db.q1("SELECT * FROM persons WHERE id=?", (person_id,))
    if r is None:
        raise HTTPException(404, "Group not found.")
    if r["name"]:
        raise HTTPException(400, "This person has a name. Remove the name first.")
    ids = [x["id"] for x in db.q("SELECT id FROM faces WHERE person_id=? AND rejected=0", (person_id,))]
    with db.tx() as c:
        c.execute("UPDATE faces SET rejected=1, person_id=NULL WHERE person_id=?", (person_id,))
        c.execute("DELETE FROM persons WHERE id=?", (person_id,))
    audit(None, user, "removed %d face(s) from face matching", len(ids))
    return {"ok": True, "face_ids": ids, "person_id": person_id}


@router.post("/faces/restore")
def restore_faces(body: dict, user: User = Depends(admin_user)):
    """Undo for the trash can: brings set-aside faces back and groups them again."""
    ids = [int(x) for x in (body.get("ids") or [])][:5000]
    if not ids:
        return {"ok": True}
    marks = ",".join("?" * len(ids))
    pid = int(body.get("person_id") or 0)
    if pid:
        # put the whole group back exactly as it was
        if db.q1("SELECT id FROM persons WHERE id=?", (pid,)) is None:
            db.ex("INSERT INTO persons(id, name, created_at) VALUES (?, NULL, ?)", (pid, time.time()))
        elif db.val("SELECT name FROM persons WHERE id=?", (pid,)):
            pid = 0
    if pid:
        db.ex(f"UPDATE faces SET rejected=0, person_id=? WHERE id IN ({marks})", [pid] + ids)
        cluster.recompute_person(pid)
        return {"ok": True, "person_id": pid}
    db.ex(f"UPDATE faces SET rejected=0, person_id=NULL WHERE id IN ({marks})", ids)
    rows = db.q(f"SELECT id, embedding FROM faces WHERE id IN ({marks}) AND length(embedding) > 0", ids)
    cl = cluster.Clusterer()
    for r in rows:
        cl.assign(r["id"], cluster.from_blob(r["embedding"]))
    cl.finish()
    return {"ok": True}


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
        try:
            person_id = _target_for_name(name, body)
        except SameName as e:
            return same_name_response(e)
        if person_id is None:
            person_id = db.ex("INSERT INTO persons(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid
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
