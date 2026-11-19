"""Duplicate finder. Never changes files on its own."""
import hashlib
import logging
import time
from collections import defaultdict
from pathlib import Path

from . import db

log = logging.getLogger("easyphotos.dupes")

SIMILAR_BITS = 4  # how many of the 64 fingerprint bits may differ for "look the same"


def _file_hash(path: Path, quick: bool) -> str | None:
    h = hashlib.sha1()
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if quick and size > 256 * 1024:
                h.update(f.read(128 * 1024))
                f.seek(-128 * 1024, 2)
                h.update(f.read(128 * 1024))
                h.update(str(size).encode())
            else:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _paths():
    return {r["id"]: r["path"] for r in db.q("SELECT id, path FROM libraries")}


def _signed_to_unsigned(v: int) -> int:
    return v + 2**64 if v < 0 else v


def rebuild(should_stop=lambda: False) -> dict:
    libs = _paths()
    base = "deleted_at IS NULL AND hidden=0 AND is_live_part=0"
    # ---- exact copies: same size, then same quick hash, then same full hash
    sizes = [r["size"] for r in db.q(f"SELECT size FROM assets WHERE {base} AND size > 0 GROUP BY size HAVING COUNT(*) > 1")]
    for size in sizes:
        if should_stop():
            return {}
        rows = db.q(f"SELECT id, library_id, path, quick_hash, full_hash FROM assets WHERE {base} AND size=?", (size,))
        for r in rows:
            if r["quick_hash"] is None:
                qh = _file_hash(Path(libs.get(r["library_id"], "")) / r["path"], quick=True)
                if qh:
                    db.ex("UPDATE assets SET quick_hash=? WHERE id=?", (qh, r["id"]))
        rows = db.q(f"SELECT id, library_id, path, quick_hash, full_hash FROM assets WHERE {base} AND size=? AND quick_hash IS NOT NULL", (size,))
        byq = defaultdict(list)
        for r in rows:
            byq[r["quick_hash"]].append(r)
        for group in byq.values():
            if len(group) < 2:
                continue
            for r in group:
                if r["full_hash"] is None:
                    fh = _file_hash(Path(libs.get(r["library_id"], "")) / r["path"], quick=False)
                    if fh:
                        db.ex("UPDATE assets SET full_hash=? WHERE id=?", (fh, r["id"]))

    exact_groups = []
    for r in db.q(f"SELECT full_hash, GROUP_CONCAT(id) AS ids FROM assets WHERE {base} AND full_hash IS NOT NULL "
                  "GROUP BY size, full_hash HAVING COUNT(*) > 1"):
        exact_groups.append(sorted(int(x) for x in r["ids"].split(",")))
    in_exact = {i for g in exact_groups for i in g}

    # ---- look the same: close fingerprints and same shape
    rows = db.q(f"SELECT id, dhash, width, height FROM assets WHERE {base} AND kind='image' AND dhash IS NOT NULL")
    items = {}
    for r in rows:
        if r["id"] in in_exact:
            continue
        if not r["width"] or not r["height"]:
            continue
        items[r["id"]] = (_signed_to_unsigned(r["dhash"]), r["width"] / r["height"])
    # skip near-blank images (all same bits) which match everything
    items = {k: v for k, v in items.items() if bin(v[0]).count("1") not in (0, 64) and 4 <= bin(v[0]).count("1") <= 60}
    buckets = defaultdict(list)
    for aid, (h, _) in items.items():
        for band in range(4):
            buckets[(band, (h >> (16 * band)) & 0xFFFF)].append(aid)
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for ids in buckets.values():
        if len(ids) < 2 or len(ids) > 200:
            continue
        for i in range(len(ids)):
            hi, ari = items[ids[i]]
            for j in range(i + 1, len(ids)):
                hj, arj = items[ids[j]]
                if bin(hi ^ hj).count("1") <= SIMILAR_BITS and abs(ari - arj) / max(ari, arj) < 0.03:
                    union(ids[i], ids[j])
    similar = defaultdict(list)
    for aid in items:
        similar[find(aid)].append(aid)
    similar_groups = [sorted(g) for g in similar.values() if len(g) > 1]

    # ---- save groups (keep the ones you already decided on)
    now = time.time()
    with db.tx() as c:
        c.execute("DELETE FROM dup_groups WHERE status='open'")
        decided = {r["signature"] for r in c.execute("SELECT signature FROM dup_groups").fetchall()}
        for kind, groups in (("exact", exact_groups), ("similar", similar_groups)):
            for g in groups:
                sig = kind + ":" + ",".join(map(str, g))
                if sig in decided:
                    continue
                cur = c.execute("INSERT INTO dup_groups(kind, signature, status, created_at) VALUES (?,?, 'open', ?)",
                                (kind, sig, now))
                gid = cur.lastrowid
                c.executemany("INSERT INTO dup_members(group_id, asset_id) VALUES (?,?)", [(gid, a) for a in g])
    log.info("Duplicates: %d exact groups, %d look-alike groups", len(exact_groups), len(similar_groups))
    return {"exact": len(exact_groups), "similar": len(similar_groups)}
