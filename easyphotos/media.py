"""Reading photos and videos: metadata, thumbnails, video helpers."""
import datetime as dt
import io
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageOps

from . import settings

log = logging.getLogger("easyphotos.media")

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    try:
        pillow_heif.register_avif_opener()
    except Exception:
        pass
except Exception:  # pragma: no cover
    pillow_heif = None

Image.MAX_IMAGE_PIXELS = 400_000_000

# EXIF tag numbers
T_MAKE, T_MODEL, T_DATETIME, T_ORIENTATION = 271, 272, 306, 274
T_EXIF_IFD, T_GPS_IFD = 0x8769, 0x8825
T_DTO, T_OFFSET_DTO, T_FNUMBER, T_EXPOSURE, T_ISO, T_FOCAL, T_LENS = 36867, 36881, 33437, 33434, 34855, 37386, 42036


# ---------------------------------------------------------------- paths

def thumb_path(asset_id: int, kind: str) -> Path:
    """kind: 't' grid thumbnail, 'p' preview, 'v' compatible video."""
    base = settings.thumbs_dir()
    sub = f"{asset_id % 1000:03d}"
    ext = {"t": None, "p": None, "v": "mp4"}[kind]
    if ext is None:
        ext = "webp" if settings.get("thumb_format") == "webp" else "jpg"
    return base / sub / f"{asset_id}_{kind}.{ext}"


def face_crop_path(face_id: int) -> Path:
    return settings.faces_dir() / f"{face_id % 1000:03d}" / f"{face_id}.webp"


# ---------------------------------------------------------------- helpers

def _ratio(v):
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]) / float(v[1]) if v[1] else None
        return float(v)
    except Exception:
        return None


def _gps_coord(values, ref):
    try:
        d, m, s = (_ratio(x) for x in values)
        if d is None:
            return None
        coord = d + (m or 0) / 60.0 + (s or 0) / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode(errors="ignore")
        if ref and ref.strip().upper() in ("S", "W"):
            coord = -coord
        return coord
    except Exception:
        return None


def _clean_str(v):
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode(errors="ignore")
    v = str(v).replace("\x00", "").strip()
    return v or None


def parse_exif_datetime(value):
    v = _clean_str(value)
    if not v:
        return None
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", v)
    if not m:
        return None
    try:
        d = dt.datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None
    if d.year < 1850 or d.year > 2100:
        return None
    return d


_NAME_DATE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?:[-_ T.]?([01]\d|2[0-3])[-_.]?([0-5]\d)[-_.]?([0-5]\d))?(?!\d)")


def date_from_filename(name: str):
    m = _NAME_DATE.search(name)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return dt.datetime(int(y), int(mo), int(d), int(h or 12), int(mi or 0), int(s or 0))
    except ValueError:
        return None


def to_ts(d: dt.datetime) -> float:
    """Sortable number for a local (camera) time. Treats it as UTC on purpose."""
    return d.replace(tzinfo=dt.timezone.utc).timestamp()


def fmt_dt(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- images

def open_image(path: str, draft_size: int | None = None) -> Image.Image:
    img = Image.open(path)
    if draft_size and img.format == "JPEG":
        try:
            img.draft("RGB", (draft_size, draft_size))
        except Exception:
            pass
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    return img


def read_image_meta(path: str) -> dict:
    """EXIF data from a photo. Never raises."""
    out = {}
    try:
        with Image.open(path) as img:
            w, h = img.size
            exif = img.getexif()
            orientation = exif.get(T_ORIENTATION, 1) if exif else 1
            if orientation in (5, 6, 7, 8):
                w, h = h, w
            out["width"], out["height"] = w, h
            if not exif:
                return out
            out["camera_make"] = _clean_str(exif.get(T_MAKE))
            out["camera_model"] = _clean_str(exif.get(T_MODEL))
            sub = {}
            try:
                sub = exif.get_ifd(T_EXIF_IFD) or {}
            except Exception:
                sub = {}
            d = parse_exif_datetime(sub.get(T_DTO)) or parse_exif_datetime(exif.get(T_DATETIME))
            if d:
                out["taken"] = d
            fn = _ratio(sub.get(T_FNUMBER)) if sub.get(T_FNUMBER) is not None else None
            if fn:
                out["fnumber"] = round(fn, 1)
            et = sub.get(T_EXPOSURE)
            if et is not None:
                r = _ratio(et)
                if r:
                    out["exposure"] = f"1/{round(1 / r)}" if r < 1 else f"{r:g}"
            iso = sub.get(T_ISO)
            if isinstance(iso, (tuple, list)):
                iso = iso[0] if iso else None
            if iso:
                try:
                    out["iso"] = int(iso)
                except Exception:
                    pass
            fl = _ratio(sub.get(T_FOCAL)) if sub.get(T_FOCAL) is not None else None
            if fl:
                out["focal"] = round(fl, 1)
            out["lens"] = _clean_str(sub.get(T_LENS))
            try:
                gps = exif.get_ifd(T_GPS_IFD) or {}
            except Exception:
                gps = {}
            if gps and 2 in gps and 4 in gps:
                lat = _gps_coord(gps[2], gps.get(1))
                lon = _gps_coord(gps[4], gps.get(3))
                if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
                    out["lat"], out["lon"] = round(lat, 6), round(lon, 6)
    except Exception as e:
        log.debug("exif failed for %s: %s", path, e)
    return out


def save_image(img: Image.Image, dest: Path, quality: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if dest.suffix == ".webp":
        img.save(tmp, "WEBP", quality=quality, method=4)
    else:
        img.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
    os.replace(tmp, dest)


def dhash(img: Image.Image) -> int:
    """64-bit difference hash used to spot photos that look the same."""
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = px[row * 9 + col]
            right = px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    if bits >= 2**63:  # store as signed 64-bit for SQLite
        bits -= 2**64
    return bits


def make_image_thumbs(asset_id: int, img: Image.Image) -> int:
    """Writes preview + grid thumbnail. Returns the dhash."""
    s = settings.get_all()
    q = int(s["thumb_quality"])
    preview = img.copy()
    preview.thumbnail((s["preview_size"], s["preview_size"]), Image.Resampling.LANCZOS)
    save_image(preview, thumb_path(asset_id, "p"), q)
    thumb = preview.copy()
    thumb.thumbnail((s["thumb_size"], s["thumb_size"]), Image.Resampling.LANCZOS)
    save_image(thumb, thumb_path(asset_id, "t"), q)
    return dhash(thumb)


# ---------------------------------------------------------------- videos

def ffprobe(path: str) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=60, check=False)
        return json.loads(out.stdout or b"{}")
    except Exception as e:
        log.debug("ffprobe failed %s: %s", path, e)
        return {}


_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def read_video_meta(path: str) -> dict:
    info = ffprobe(path)
    out = {}
    fmt = info.get("format", {}) or {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    try:
        out["duration"] = round(float(fmt.get("duration", 0)), 2) or None
    except Exception:
        pass
    vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if vstream:
        w, h = vstream.get("width"), vstream.get("height")
        rot = 0
        try:
            rot = int((vstream.get("tags") or {}).get("rotate", 0))
        except Exception:
            rot = 0
        for sd in vstream.get("side_data_list", []) or []:
            if "rotation" in sd:
                try:
                    rot = int(sd["rotation"])
                except Exception:
                    pass
        if w and h:
            if abs(rot) in (90, 270):
                w, h = h, w
            out["width"], out["height"] = w, h
        out["video_codec"] = vstream.get("codec_name")
        stags = {k.lower(): v for k, v in (vstream.get("tags") or {}).items()}
        tags = {**stags, **tags}
    created = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if created:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?", created)
        if m:
            try:
                d = dt.datetime(*(int(x) for x in m.groups()[:6]))
                tz = m.group(7)
                if tz == "Z":
                    # creation_time is UTC; convert to the server's local time
                    d = d.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
                out["taken"] = d
            except ValueError:
                pass
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    if loc:
        m = _ISO6709.match(loc)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
                out["lat"], out["lon"] = round(lat, 6), round(lon, 6)
    make = tags.get("com.apple.quicktime.make")
    model = tags.get("com.apple.quicktime.model")
    if make:
        out["camera_make"] = make
    if model:
        out["camera_model"] = model
    return out


def video_poster(path: str, duration) -> Image.Image | None:
    at = 1.0 if (duration or 0) > 2 else 0.0
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
             "-vf", "scale='min(1920,iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, timeout=120, check=False)
        if not res.stdout and at > 0:
            res = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, timeout=120, check=False)
        if res.stdout:
            return Image.open(io.BytesIO(res.stdout)).convert("RGB")
    except Exception as e:
        log.debug("poster failed %s: %s", path, e)
    return None


_transcode_locks: dict[int, threading.Lock] = {}
_transcode_guard = threading.Lock()


def compat_video(asset_id: int, src: str) -> Path | None:
    """Converts a video to H.264 MP4 the first time it is needed, then reuses it."""
    dest = thumb_path(asset_id, "v")
    if dest.exists():
        return dest
    with _transcode_guard:
        lock = _transcode_locks.setdefault(asset_id, threading.Lock())
    with lock:
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp.mp4")
        cmd = ["nice", "-n", "10", "ffmpeg", "-v", "error", "-y", "-i", src,
               "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-vf", "scale='min(1920,iw)':-2", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=3600, check=False)
            if res.returncode == 0 and tmp.exists():
                os.replace(tmp, dest)
                return dest
            log.warning("transcode failed for %s: %s", src, res.stderr[-500:])
        except FileNotFoundError:
            res = None
        except Exception as e:
            log.warning("transcode error %s: %s", src, e)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    return None


def remove_derivatives(asset_id: int) -> None:
    for k in ("t", "p", "v"):
        try:
            thumb_path(asset_id, k).unlink(missing_ok=True)
        except Exception:
            pass
    # Older format leftovers
    base = settings.thumbs_dir() / f"{asset_id % 1000:03d}"
    for ext in ("webp", "jpg"):
        for k in ("t", "p"):
            try:
                (base / f"{asset_id}_{k}.{ext}").unlink(missing_ok=True)
            except Exception:
                pass
