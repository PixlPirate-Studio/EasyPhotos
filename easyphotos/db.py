"""Database access.

One SQLite file in the config folder. If EP_DB_KEY is set the file is
encrypted with SQLCipher. Each thread gets its own connection.
"""
import json
import logging
import threading
import time

from . import config

log = logging.getLogger("easyphotos.db")

try:  # SQLCipher build of sqlite (encrypted database support)
    import sqlcipher3.dbapi2 as _sqlcipher
except Exception:  # pragma: no cover
    _sqlcipher = None

import sqlite3 as _sqlite

_local = threading.local()

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('viewer','user','admin')),
    password_hash TEXT,
    all_libraries INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS user_libraries (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, library_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS libraries (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scan_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    ext TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    width INTEGER, height INTEGER,
    taken_at TEXT,
    taken_ts REAL,
    date_source TEXT,
    lat REAL, lon REAL,
    city TEXT, state TEXT, country TEXT,
    camera_make TEXT, camera_model TEXT, lens TEXT,
    fnumber REAL, exposure TEXT, focal REAL, iso INTEGER,
    duration REAL, video_codec TEXT,
    description TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    deleted_at REAL,
    recycle_path TEXT,
    meta_status INTEGER NOT NULL DEFAULT 0,
    face_status INTEGER NOT NULL DEFAULT 0,
    dhash INTEGER,
    quick_hash TEXT,
    full_hash TEXT,
    loc_source TEXT,
    live_video_id INTEGER,
    is_live_part INTEGER NOT NULL DEFAULT 0,
    xmp_dirty INTEGER NOT NULL DEFAULT 0,
    thumb_ver INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    added_at REAL NOT NULL,
    UNIQUE (library_id, path)
);
CREATE INDEX IF NOT EXISTS idx_assets_taken ON assets(taken_ts DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_assets_meta ON assets(meta_status);
CREATE INDEX IF NOT EXISTS idx_assets_face ON assets(face_status);
CREATE INDEX IF NOT EXISTS idx_assets_folder ON assets(library_id, folder);
CREATE INDEX IF NOT EXISTS idx_assets_city ON assets(city);
CREATE INDEX IF NOT EXISTS idx_assets_size ON assets(size);
CREATE INDEX IF NOT EXISTS idx_assets_dhash ON assets(dhash);

CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY,
    name TEXT COLLATE NOCASE,
    hidden INTEGER NOT NULL DEFAULT 0,
    unmatched INTEGER NOT NULL DEFAULT 0,
    cover_face_id INTEGER,
    centroid BLOB,
    face_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_persons_name ON persons(name);

CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
    score REAL,
    embedding BLOB,
    manual INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faces_asset ON faces(asset_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    added_at REAL NOT NULL,
    PRIMARY KEY (asset_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag_id);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS album_assets (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    added_at REAL NOT NULL,
    PRIMARY KEY (album_id, asset_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, asset_id)
);

CREATE TABLE IF NOT EXISTS dup_groups (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    signature TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dup_members (
    group_id INTEGER NOT NULL REFERENCES dup_groups(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, asset_id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    folders TEXT NOT NULL DEFAULT '[]',
    include_videos INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS job_status (
    name TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'idle',
    message TEXT,
    rate REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS job_commands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    args TEXT,
    created_at REAL NOT NULL
);
"""


def _connect():
    config.ensure_dirs()
    if config.DB_KEY and _sqlcipher is not None:
        conn = _sqlcipher.connect(str(config.DB_PATH), timeout=30, check_same_thread=False, isolation_level=None)
        conn.row_factory = _sqlcipher.Row
        key = config.DB_KEY.replace("'", "''")
        conn.execute(f"PRAGMA key = '{key}'")
    else:
        if config.DB_KEY and _sqlcipher is None:
            log.warning("EP_DB_KEY is set but SQLCipher is not available; database is NOT encrypted")
        conn = _sqlite.connect(str(config.DB_PATH), timeout=30, check_same_thread=False, isolation_level=None)
        conn.row_factory = _sqlite.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    c.commit()


def q(sql: str, params=()):
    return conn().execute(sql, params).fetchall()


def q1(sql: str, params=()):
    return conn().execute(sql, params).fetchone()


def val(sql: str, params=(), default=None):
    row = conn().execute(sql, params).fetchone()
    if row is None:
        return default
    v = row[0]
    return default if v is None else v


def ex(sql: str, params=()):
    c = conn()
    for attempt in range(5):
        try:
            cur = c.execute(sql, params)
            c.commit()
            return cur
        except Exception as e:  # database is locked -> retry
            if "locked" in str(e).lower() and attempt < 4:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise


def exmany(sql: str, seq):
    with tx() as c:
        c.executemany(sql, seq)


class tx:
    """Context manager for a group of writes in one transaction."""

    def __enter__(self):
        self.c = conn()
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.c.commit()
        else:
            self.c.rollback()
        return False


def row_dict(row):
    return dict(row) if row is not None else None


def rows_dicts(rows):
    return [dict(r) for r in rows]


def jdump(v) -> str:
    return json.dumps(v, separators=(",", ":"))


def checkpoint() -> None:
    try:
        conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
