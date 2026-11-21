"""Admin-editable settings stored in the database."""
import json
from pathlib import Path

from . import config, db

DEFAULTS = {
    # Thumbnails
    "thumb_size": 360,          # longest side of grid thumbnails (px)
    "preview_size": 1440,       # longest side of full-screen previews (px)
    "thumb_format": "webp",     # webp | jpeg
    "thumb_quality": 80,
    # Storage
    "thumbs_dir": "",           # empty = default (/thumbs, or /config/thumbs if not mapped)
    "faces_dir": "",            # empty = same as thumbnails
    # Face recognition
    "faces_enabled": True,
    "faces_started": False,     # becomes True after the admin clicks "Find faces"
    "face_strictness": 50,      # 0 = loose, 100 = strict
    "face_min_size": 40,        # ignore faces smaller than this (px, on the preview image)
    "face_min_score": 0.7,      # detector confidence
    "cluster_min_faces": 3,     # groups smaller than this show under "Single faces"
    # Background speed
    "worker_threads": 4,
    "night_boost": True,
    "night_threads": 8,
    "night_start": 1,
    "night_end": 7,
    "paused": False,
    "scan_interval_hours": 6,
    # Recycle bin
    "allow_delete": True,
    "recycle_auto_days": 0,     # 0 = never empty automatically
    # Optional extras
    "duplicates_enabled": True,
    "quality_enabled": True,
    "faces_favorites_only": False,
    "places_enabled": True,
    "xmp_enabled": False,
    "video_compat": True,
    # What the Unraid container log shows: "files" = every file, "summary" = a progress line each minute
    "log_detail": "files",
}

_INT_LIMITS = {
    "thumb_size": (120, 1000),
    "preview_size": (720, 4096),
    "thumb_quality": (40, 95),
    "face_strictness": (0, 100),
    "face_min_size": (16, 400),
    "cluster_min_faces": (2, 20),
    "worker_threads": (1, 64),
    "night_threads": (1, 64),
    "night_start": (0, 23),
    "night_end": (0, 23),
    "scan_interval_hours": (0, 168),
    "recycle_auto_days": (0, 3650),
}


def get_all() -> dict:
    out = dict(DEFAULTS)
    for row in db.q("SELECT key, value FROM settings"):
        if row["key"] in DEFAULTS:
            try:
                out[row["key"]] = json.loads(row["value"])
            except Exception:
                pass
    return out


def get(key: str):
    row = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row["value"])
    except Exception:
        return DEFAULTS.get(key)


def clean(key: str, value):
    """Validate one value; raises ValueError when it is not allowed."""
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting: {key}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        v = int(value)
        lo, hi = _INT_LIMITS.get(key, (-10**9, 10**9))
        return max(lo, min(hi, v))
    if isinstance(default, float):
        return max(0.1, min(0.99, float(value)))
    v = str(value or "").strip()
    if key == "log_detail" and v not in ("files", "summary"):
        raise ValueError("Log detail must be files or summary")
    if key == "thumb_format" and v not in ("webp", "jpeg"):
        raise ValueError("Format must be webp or jpeg")
    if key in ("thumbs_dir", "faces_dir") and v:
        if not v.startswith("/"):
            raise ValueError("Folder must be a full path starting with /")
    return v


def set_many(values: dict) -> dict:
    cleaned = {k: clean(k, v) for k, v in values.items()}
    with db.tx() as c:
        for k, v in cleaned.items():
            c.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, json.dumps(v)))
    return cleaned


def thumbs_dir() -> Path:
    custom = get("thumbs_dir")
    if custom:
        return Path(custom)
    if config.DEFAULT_THUMBS_DIR.exists():
        return config.DEFAULT_THUMBS_DIR
    return config.CONFIG_DIR / "thumbs"


def faces_dir() -> Path:
    custom = get("faces_dir")
    if custom:
        return Path(custom)
    return thumbs_dir() / "faces"


def similarity_threshold(strictness=None) -> float:
    """Cosine similarity two faces need to count as the same person."""
    s = get("face_strictness") if strictness is None else strictness
    return 0.35 + (max(0, min(100, int(s))) / 100.0) * 0.30
