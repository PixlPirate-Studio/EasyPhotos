"""Grouping faces into people.

Every person (named or not) keeps an average "fingerprint" (centroid). A new
face joins the closest person when it is similar enough (the strictness
setting), otherwise it starts a new group. Naming, moving and removing faces
recomputes the averages, so fixes make future matching better.
"""
import time

import numpy as np

from . import db, settings
from .faces import from_blob, to_blob


def _norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def recompute_person(pid: int) -> None:
    rows = db.q("SELECT id, embedding, score, confirmed FROM faces WHERE person_id=? AND rejected=0 AND length(embedding) > 0", (pid,))
    if not rows:
        p = db.q1("SELECT name FROM persons WHERE id=?", (pid,))
        if p is not None and not p["name"]:
            db.ex("DELETE FROM persons WHERE id=?", (pid,))
        else:
            db.ex("UPDATE persons SET face_count=0, centroid=NULL, cover_face_id=NULL WHERE id=?", (pid,))
        return
    embs = np.stack([from_blob(r["embedding"]) for r in rows])
    centroid = _norm(embs.mean(axis=0))
    cover = db.val("SELECT cover_face_id FROM persons WHERE id=?", (pid,))
    ids = {r["id"] for r in rows}
    if cover not in ids:
        # Pick the clearest face closest to the average
        sims = embs @ centroid
        scores = np.array([r["score"] or 0.5 for r in rows])
        cover = rows[int(np.argmax(sims + 0.3 * scores))]["id"]
    db.ex("UPDATE persons SET centroid=?, face_count=?, cover_face_id=? WHERE id=?",
          (to_blob(centroid), len(rows), cover, pid))


class Clusterer:
    """Keeps person centroids in memory while assigning a batch of faces."""

    def __init__(self):
        self.thr = settings.similarity_threshold()
        rows = db.q("SELECT id, centroid, face_count FROM persons WHERE centroid IS NOT NULL")
        self.ids = [r["id"] for r in rows]
        self.counts = [r["face_count"] for r in rows]
        self.mat = np.stack([from_blob(r["centroid"]) for r in rows]) if rows else np.zeros((0, 512), np.float32)
        self.touched = set()

    def assign(self, face_id: int, emb: np.ndarray) -> int:
        best_i, best_sim = -1, -1.0
        if len(self.ids):
            sims = self.mat @ emb
            best_i = int(np.argmax(sims))
            best_sim = float(sims[best_i])
        if best_i >= 0 and best_sim >= self.thr:
            pid = self.ids[best_i]
            n = self.counts[best_i]
            self.mat[best_i] = _norm(self.mat[best_i] * n + emb)
            self.counts[best_i] = n + 1
        else:
            cur = db.ex("INSERT INTO persons(name, centroid, face_count, created_at) VALUES (NULL, ?, 0, ?)",
                        (to_blob(emb), time.time()))
            pid = cur.lastrowid
            self.ids.append(pid)
            self.counts.append(1)
            self.mat = np.vstack([self.mat, emb[None, :]]) if len(self.mat) else emb[None, :].copy()
        db.ex("UPDATE faces SET person_id=? WHERE id=?", (pid, face_id))
        self.touched.add(pid)
        return pid

    def finish(self):
        for pid in self.touched:
            recompute_person(pid)
        self.touched.clear()


def recluster_unnamed() -> int:
    """Re-groups every face that is not attached to a named person.
    Used after changing the strictness setting."""
    faces = db.q(
        "SELECT f.id, f.embedding FROM faces f LEFT JOIN persons p ON p.id=f.person_id "
        "WHERE f.rejected=0 AND length(f.embedding) > 0 AND (f.person_id IS NULL OR (p.name IS NULL AND p.unmatched=0))")
    with db.tx() as c:
        c.execute("UPDATE faces SET person_id=NULL WHERE person_id IN (SELECT id FROM persons WHERE name IS NULL AND unmatched=0)")
        c.execute("DELETE FROM persons WHERE name IS NULL AND unmatched=0")
    cl = Clusterer()
    for r in faces:
        cl.assign(r["id"], from_blob(r["embedding"]))
    cl.finish()
    return len(faces)


def suggestion_for(centroid_blob, named_cache) -> tuple[int, str] | None:
    """'Looks like X?' — nearest named person that is close but not close enough."""
    if not centroid_blob or not named_cache["ids"]:
        return None
    v = from_blob(centroid_blob)
    sims = named_cache["mat"] @ v
    i = int(np.argmax(sims))
    s = float(sims[i])
    thr = settings.similarity_threshold()
    if thr - 0.15 <= s:
        return named_cache["ids"][i], named_cache["names"][i]
    return None


def named_cache():
    rows = db.q("SELECT id, name, centroid FROM persons WHERE name IS NOT NULL AND centroid IS NOT NULL")
    return {
        "ids": [r["id"] for r in rows],
        "names": [r["name"] for r in rows],
        "mat": np.stack([from_blob(r["centroid"]) for r in rows]) if rows else np.zeros((0, 512), np.float32),
    }


def merge_persons(src: int, dest: int) -> None:
    if src == dest:
        return
    db.ex("UPDATE faces SET person_id=? WHERE person_id=?", (dest, src))
    db.ex("DELETE FROM persons WHERE id=?", (src,))
    recompute_person(dest)
