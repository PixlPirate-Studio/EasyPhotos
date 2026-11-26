"""Paths and environment settings.

Everything here comes from environment variables so the Unraid template can
change it. Settings that the admin can change from the web page live in the
database instead (see settings.py).
"""
import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


# Where the database, backups and logs live (map to appdata).
CONFIG_DIR = Path(_env("EP_CONFIG_DIR", "/config"))

# Default place for thumbnails. The admin can point this somewhere else in
# Admin settings > Storage locations (for example a share on the array).
DEFAULT_THUMBS_DIR = Path(_env("EP_THUMBS_DIR", "/thumbs"))

# Folder that holds the photo libraries mapped into the container. The admin
# browses inside this folder when adding a library.
LIBRARIES_ROOT = Path(_env("EP_LIBRARIES_ROOT", "/libraries"))

# Face AI models and offline place-name data baked into the image.
MODELS_DIR = Path(_env("EP_MODELS_DIR", "/opt/easyphotos/models"))
GEO_FILE = Path(_env("EP_GEO_FILE", "/opt/easyphotos/geo/geo.npz"))

# Optional database encryption key. When set, the database file is encrypted
# with SQLCipher. Set it on the very first start and never change it.
DB_KEY = os.environ.get("EP_DB_KEY", "")

PORT = int(_env("EP_PORT", "8080"))

# Front-end files.
WEB_DIR = Path(_env("EP_WEB_DIR", str(Path(__file__).resolve().parent.parent / "web")))

DB_PATH = CONFIG_DIR / "easyphotos.db"
BACKUP_DIR = CONFIG_DIR / "backups"
LOG_DIR = CONFIG_DIR / "logs"

# Camera RAW files: shown using the full-size preview the camera stores inside them.
RAW_EXTS = {
    ".rw2", ".raw", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".orf", ".raf",
    ".pef", ".dng", ".srw", ".x3f", ".3fr", ".erf", ".kdc", ".mrw", ".rwl", ".iiq", ".mef", ".mos",
}
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".heic", ".heif", ".hif",
    ".gif", ".bmp", ".tif", ".tiff", ".avif",
} | RAW_EXTS
VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg",
}

# Folders the scanner never walks into.
SKIP_DIRS = {".recycle", "@eaDir", ".thumbnails", "#recycle", "$RECYCLE.BIN", "System Volume Information", ".Trash-0"}


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, BACKUP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
