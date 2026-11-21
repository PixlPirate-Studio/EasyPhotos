"""Spots blurry and too-dark photos so the admin can review them.

How blur is measured (a "re-blur" test): blur the photo a little more and
see how much detail disappears. A sharp photo loses a lot; an already
blurry one hardly changes. That works the same for busy and plain
subjects. It's checked in four directions, so camera shake (blur in one
direction) is caught too, and in 16 parts of the photo: if any part is
sharp (say, a face in front of a soft background), the photo counts as sharp.

The score runs from 0 (very blurry) to 100. Sharp photos score about 60+.
"""
import cv2
import numpy as np

from . import db, media

LEVELS = {"very": 30.0, "blurry": 45.0, "soft": 55.0}
DARK = 22.0  # average brightness (0-255) below this is "too dark"
DARK_BLANK = 45.0  # a photo with no detail at all only counts as dark below this

# SQL for "too dark": very dark, or dim and without any detail (pocket shot, lens cap)
DARK_SQL = "(brightness < 22 OR (sharpness IS NULL AND brightness < 45))"

_DIRS = [(0, 1), (1, 0), (1, 1), (1, -1)]
_KERNELS = {}


def _line_kernel(dy, dx, n=9):
    key = (dy, dx, n)
    if key not in _KERNELS:
        k = np.zeros((n, n), np.float32)
        c = n // 2
        for t in range(-c, c + 1):
            k[c + t * dy, c + t * dx] = 1
        _KERNELS[key] = k / k.sum()
    return _KERNELS[key]


def _diff(g, dy, dx):
    """Absolute difference between each pixel and its neighbour (dy, dx)."""
    H, W = g.shape
    out = np.zeros_like(g)
    ys, ye = max(dy, 0), H + min(dy, 0)
    xs, xe = max(dx, 0), W + min(dx, 0)
    out[ys:ye, xs:xe] = np.abs(g[ys:ye, xs:xe] - g[ys - dy:ye - dy, xs - dx:xe - dx])
    return out


def measure(gray: np.ndarray, grid: int = 4):
    """gray: 8-bit grayscale image. Returns (sharpness 0-100 or None, brightness 0-255).
    Sharpness is None when the photo has no detail at all (black, lens cap, plain wall)."""
    h, w = gray.shape[:2]
    s = 640 / max(h, w)
    if s < 1:
        gray = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    brightness = float(gray.mean())
    g = gray.astype(np.float32)
    H, W = g.shape
    per_dir = []
    for dy, dx in _DIRS:
        d0 = _diff(g, dy, dx)
        d1 = _diff(cv2.filter2D(g, -1, _line_kernel(dy, dx)), dy, dx)
        per_dir.append((d0, np.maximum(0, d0 - d1)))
    tiles = []
    for i in range(grid):
        for j in range(grid):
            sl = (slice(i * H // grid, (i + 1) * H // grid), slice(j * W // grid, (j + 1) * W // grid))
            texture, dirs = 0.0, []
            for d0, v in per_dir:
                s0 = float(d0[sl].sum())
                n = d0[sl].size or 1
                texture = max(texture, s0 / n)
                dirs.append(float(v[sl].sum()) / s0 if s0 > 0 else 0.0)
            if texture < 2.0:  # flat area (sky, wall): says nothing about blur
                continue
            tiles.append(min(dirs))  # the blurriest direction counts
    if not tiles:
        return None, brightness
    tiles.sort(reverse=True)
    best = tiles[:2]
    return round(100 * sum(best) / len(best), 1), brightness


def check(asset_id: int):
    """Measures one photo from its preview image. Returns (sharpness, brightness)."""
    p = media.thumb_path(asset_id, "p")
    data = np.fromfile(str(p), np.uint8)
    gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("preview could not be read")
    return measure(gray)


def counts(level: str = "blurry"):
    thr = LEVELS.get(level, LEVELS["blurry"])
    base = "kind='image' AND meta_status=1 AND deleted_at IS NULL AND hidden=0 AND is_live_part=0"
    return {
        "total": db.val(f"SELECT COUNT(*) FROM assets WHERE {base}", default=0),
        "checked": db.val(f"SELECT COUNT(*) FROM assets WHERE {base} AND quality_status<>0", default=0),
        "very": db.val(f"SELECT COUNT(*) FROM assets WHERE {base} AND quality_ok=0 AND sharpness < ?", (LEVELS['very'],), default=0),
        "blurry": db.val(f"SELECT COUNT(*) FROM assets WHERE {base} AND quality_ok=0 AND sharpness < ?", (LEVELS['blurry'],), default=0),
        "soft": db.val(f"SELECT COUNT(*) FROM assets WHERE {base} AND quality_ok=0 AND sharpness < ?", (LEVELS['soft'],), default=0),
        "dark": db.val(f"SELECT COUNT(*) FROM assets WHERE {base} AND quality_ok=0 AND quality_status=1 AND {DARK_SQL}", default=0),
        "level": level, "threshold": thr,
    }
