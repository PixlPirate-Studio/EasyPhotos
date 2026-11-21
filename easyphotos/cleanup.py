"""Runs the "Empty folders" and "Non-photo files" checks in the background
worker and keeps the last result, so the admin can close the page and come
back later. Results are kept in the cleanup_runs table."""
import json
import logging
import os
import threading
import time

from . import db, emptydirs, otherfiles

log = logging.getLogger("easyphotos.worker")

NAMES = {"empty_folders": "empty folders", "other_files": "non-photo files"}


class Stopped(Exception):
    pass


def _save(name, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    db.ex("INSERT OR IGNORE INTO cleanup_runs(name) VALUES (?)", (name,))
    db.ex(f"UPDATE cleanup_runs SET {cols} WHERE name=?", (*fields.values(), name))


def get(name):
    r = db.q1("SELECT * FROM cleanup_runs WHERE name=?", (name,))
    if not r:
        return {"state": "never", "libraries": []}
    out = {"state": r["state"], "started_at": r["started_at"], "finished_at": r["finished_at"],
           "checked": r["checked"], "current": r["current"], "error": r["error"], "libraries": []}
    if r["data"]:
        try:
            out["libraries"] = json.loads(r["data"])
        except ValueError:
            pass
    return out


def queue(name):
    """Called by the web page: marks the check as waiting for the worker."""
    _save(name, state="queued", started_at=time.time(), checked=0, current="Waiting for the background worker…", error=None)


def forget_items(name, removed):
    """Takes removed folders/files out of the saved result so the list stays right.
    removed: set of (library_id, rel)."""
    if not removed:
        return
    r = db.q1("SELECT data FROM cleanup_runs WHERE name=?", (name,))
    if not r or not r["data"]:
        return
    libs = json.loads(r["data"])
    key = "folders" if name == "empty_folders" else "files"
    for lib in libs:
        lib[key] = [x for x in lib[key] if (lib["id"], x["rel"]) not in removed]
    _save(name, data=json.dumps(libs))


def mark_interrupted():
    """The worker (re)started: anything that was running didn't finish."""
    db.ex("UPDATE cleanup_runs SET state='stopped', current=NULL, error='The server restarted before it finished.' "
          "WHERE state IN ('running', 'queued')")


def run(name, stop: threading.Event, activity):
    started = time.time()
    _save(name, state="running", started_at=started, finished_at=None, checked=0, current="Starting…", error=None)
    activity([("cleanup", "info", f"Started looking for {NAMES[name]}")])
    count = {"n": 0, "last": 0.0}

    def progress(path):
        if stop.is_set():
            raise Stopped()
        count["n"] += 1
        now = time.time()
        if now - count["last"] > 1.0:
            count["last"] = now
            _save(name, checked=count["n"], current=path)

    libs = []
    try:
        for r in db.q("SELECT * FROM libraries ORDER BY name"):
            path = r["path"]
            exists = os.path.isdir(path)
            entry = {"id": r["id"], "name": r["name"], "path": path, "exists": exists,
                     "writable": os.access(path, os.W_OK) if exists else False}
            if name == "empty_folders":
                found = emptydirs.find(path, limit=20000, progress=progress) if exists else []
                entry["capped"] = len(found) >= 20000
                entry["folders"] = [{"rel": f["rel"], "subfolders": f["subfolders"], "junk": f["junk"],
                                     "junk_count": f["junk_count"]} for f in found]
            else:
                files = otherfiles.find(path, progress=progress) if exists else []
                entry["files"] = files
                entry["capped"] = len(files) >= 20000
            libs.append(entry)
        total = sum(len(x.get("folders") or x.get("files") or []) for x in libs)
        _save(name, state="done", finished_at=time.time(), checked=count["n"], current=None, data=json.dumps(libs))
        activity([("cleanup", "info", f"Finished looking for {NAMES[name]}: found {total:,} "
                                      f"after checking {count['n']:,} folders in {time.time() - started:.0f} s")])
    except Stopped:
        _save(name, state="stopped", finished_at=time.time(), current=None, error="Stopped before it finished.")
        activity([("cleanup", "info", f"Stopped looking for {NAMES[name]}")])
    except Exception as e:  # never take the worker down
        log.exception("Looking for %s failed", NAMES[name])
        _save(name, state="error", finished_at=time.time(), current=None, error=str(e)[:300])
        activity([("cleanup", "error", f"Looking for {NAMES[name]} failed: {e}")])
