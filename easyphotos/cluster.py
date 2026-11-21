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


def _neighbours(X: np.ndarray, thr: float, k: int = 15, chunk: int = 512):
    """For every face, its closest faces that are similar enough (edge list)."""
    n = len(X)
    k = min(k, n - 1)
    rows, cols, ws = [], [], []
    if k <= 0:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0, np.float32)
    for start in range(0, n, chunk):
        sims = X[start:start + chunk] @ X.T
        b = len(sims)
        sims[np.arange(b), np.arange(start, start + b)] = -1.0
        idx = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        w = np.take_along_axis(sims, idx, axis=1)
        keep = w >= thr
        r = np.repeat(np.arange(start, start + b), k).reshape(b, k)
        rows.append(r[keep]); cols.append(idx[keep]); ws.append(w[keep])
    return np.concatenate(rows), np.concatenate(cols), np.concatenate(ws).astype(np.float32)


def group_faces(X: np.ndarray, thr: float, iterations: int = 25, seed: int = 7) -> np.ndarray:
    """Groups all faces at once (graph clustering, "Chinese whispers").

    Each face links to its closest look-alikes above the threshold. Then each
    face repeatedly takes the group that most of its strongest links belong
    to. Unlike a running average, this doesn't drift, so two similar-looking
    people don't slowly melt into one group. Finally, faces that don't fit
    their group's average well are split off on their own.
    Returns a group number for every face."""
    n = len(X)
    if n == 0:
        return np.zeros(0, int)
    X = np.ascontiguousarray(X, dtype=np.float32)
    r, c, w = _neighbours(X, thr)
    # make links two-way, keeping each pair once
    a = np.concatenate([r, c]); b = np.concatenate([c, r]); ww = np.concatenate([w, w])
    order = np.lexsort((b, a))
    a, b, ww = a[order], b[order], ww[order]
    if len(a):
        first = np.ones(len(a), bool)
        first[1:] = (a[1:] != a[:-1]) | (b[1:] != b[:-1])
        a, b, ww = a[first], b[first], ww[first]
    starts = np.searchsorted(a, np.arange(n + 1))
    nbr = [b[starts[i]:starts[i + 1]].tolist() for i in range(n)]
    wts = [(ww[starts[i]:starts[i + 1]] ** 2).tolist() for i in range(n)]  # favour strong links
    labels = list(range(n))
    rng = np.random.default_rng(seed)
    for _ in range(iterations):
        changed = 0
        for i in rng.permutation(n).tolist():
            nb = nbr[i]
            if not nb:
                continue
            votes = {}
            for j, wt in zip(nb, wts[i]):
                lj = labels[j]
                votes[lj] = votes.get(lj, 0.0) + wt
            best = max(votes, key=votes.get)
            if best != labels[i]:
                labels[i] = best
                changed += 1
        if changed == 0:
            break
    labels = np.array(labels)
    # split off faces that don't match their group's average
    out = labels.copy()
    nxt = n
    for g in np.unique(labels):
        m = np.where(labels == g)[0]
        if len(m) < 2:
            continue
        cen = _norm(X[m].mean(axis=0))
        bad = m[(X[m] @ cen) < thr - 0.05]
        for i in bad:
            out[i] = nxt
            nxt += 1
    _, out = np.unique(out, return_inverse=True)
    # merging is done by average: a little looser than the face-to-face link
    return _merge_groups(X, out, thr - 0.08)


def _merge_groups(X: np.ndarray, labels: np.ndarray, thr: float, max_groups: int = 6000) -> np.ndarray:
    """Joins groups that are the same person on average ("average linkage"):
    two groups merge only if their faces match each other well on average,
    not just through one look-alike pair. Single faces then join a group the
    same way."""
    labels = labels.copy()
    ids, inv, sizes = np.unique(labels, return_inverse=True, return_counts=True)
    sums = np.zeros((len(ids), X.shape[1]), np.float64)
    np.add.at(sums, inv, X)
    multi = np.where(sizes >= 2)[0]
    if len(multi) > max_groups:  # keep memory in check on huge libraries: biggest groups first
        multi = multi[np.argsort(-sizes[multi])[:max_groups]]
    if len(multi) >= 2:
        M = (sums[multi] / sizes[multi, None]).astype(np.float32)
        n = sizes[multi].astype(np.float64)
        S = M @ M.T
        np.fill_diagonal(S, -1)
        alive = np.ones(len(multi), bool)
        parent = np.arange(len(multi))
        best_j = np.argmax(S, axis=1)
        best_s = S[np.arange(len(S)), best_j]
        while True:
            i = int(np.argmax(best_s))
            j = int(best_j[i])
            if best_s[i] < thr:
                break
            # merge j into i
            M[i] = (M[i] * n[i] + M[j] * n[j]) / (n[i] + n[j])
            n[i] += n[j]
            alive[j] = False
            parent[j] = i
            S[j, :] = -1
            S[:, j] = -1
            best_s[j] = -1
            row = M @ M[i]
            row[~alive] = -1
            row[i] = -1
            S[i, :] = row
            S[:, i] = row
            best_j[i] = int(np.argmax(row))
            best_s[i] = row[best_j[i]]
            # rows that pointed at i or j need a fresh look; others may now prefer i
            stale = np.where(alive & ((best_j == i) | (best_j == j)))[0]
            for r in stale:
                if r == i:
                    continue
                best_j[r] = int(np.argmax(S[r]))
                best_s[r] = S[r, best_j[r]]
            better = alive & (row > best_s)
            best_j[better] = i
            best_s[better] = row[better]
        def root(k):
            while parent[k] != k:
                k = parent[k]
            return k
        new_of = {int(ids[multi[k]]): int(ids[multi[root(k)]]) for k in range(len(multi))}
        labels = np.array([new_of.get(int(l), int(l)) for l in labels])
        # recompute group sums after merging
        ids, inv, sizes = np.unique(labels, return_inverse=True, return_counts=True)
        sums = np.zeros((len(ids), X.shape[1]), np.float64)
        np.add.at(sums, inv, X)
    # single faces: join the group they match best on average, if good enough
    big = np.where(sizes >= 2)[0]
    singles = np.where(sizes == 1)[0]
    if len(big) and len(singles):
        M = (sums[big] / sizes[big, None]).astype(np.float32)
        face_of = {int(g): int(np.where(inv == g)[0][0]) for g in singles}
        for start in range(0, len(singles), 2048):
            grp = singles[start:start + 2048]
            fx = X[[face_of[int(g)] for g in grp]]
            sims = fx @ M.T
            best = np.argmax(sims, axis=1)
            for g, b, sv in zip(grp, best, sims[np.arange(len(grp)), best]):
                if sv >= thr:
                    labels[face_of[int(g)]] = ids[big[b]]
    _, labels = np.unique(labels, return_inverse=True)
    return labels


def recluster_unnamed() -> int:
    """Re-groups every face that is not attached to a named person, all at
    once (see group_faces). Named people and their faces are never changed;
    a new group whose faces clearly match a named person joins that person."""
    thr = settings.similarity_threshold()
    faces = db.q(
        "SELECT f.id, f.embedding FROM faces f LEFT JOIN persons p ON p.id=f.person_id "
        "WHERE f.rejected=0 AND length(f.embedding) > 0 AND (f.person_id IS NULL OR (p.name IS NULL AND p.unmatched=0)) "
        "ORDER BY f.id")
    named = named_cache()
    if not faces:
        with db.tx() as c:
            c.execute("DELETE FROM persons WHERE name IS NULL AND unmatched=0")
        return 0
    X = np.stack([from_blob(r["embedding"]) for r in faces]).astype(np.float32)
    labels = group_faces(X, thr)
    # Work out every group first, then save it all in one go: if anything
    # fails before that, the existing groups are left exactly as they were.
    plan = []
    for g in np.unique(labels):
        idx = np.where(labels == g)[0]
        cen = _norm(X[idx].mean(axis=0))
        pid = None
        if named["ids"]:
            sims = named["mat"] @ cen
            k = int(np.argmax(sims))
            if sims[k] >= thr:
                pid = named["ids"][k]
        plan.append((pid, cen, [faces[i]["id"] for i in idx]))
    touched = set()
    now = time.time()
    with db.tx() as c:
        c.execute("UPDATE faces SET person_id=NULL WHERE person_id IN (SELECT id FROM persons WHERE name IS NULL AND unmatched=0)")
        c.execute("DELETE FROM persons WHERE name IS NULL AND unmatched=0")
        for pid, cen, fids in plan:
            if pid is None:
                pid = c.execute("INSERT INTO persons(name, centroid, face_count, created_at) VALUES (NULL, ?, ?, ?)",
                                (to_blob(cen), len(fids), now)).lastrowid
            touched.add(pid)
            c.executemany("UPDATE faces SET person_id=? WHERE id=?", [(pid, f) for f in fids])
    for pid in touched:
        recompute_person(pid)
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
