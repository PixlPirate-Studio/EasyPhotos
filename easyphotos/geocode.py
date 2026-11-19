"""Offline place names: turns GPS coordinates into "City, State, Country".

Uses a GeoNames city list (towns with 1,000+ people) bundled into the image.
Nothing is sent to the internet.
"""
import logging
import threading

import numpy as np

from . import config

log = logging.getLogger("easyphotos.geo")

_lock = threading.Lock()
_data = None


def _load():
    global _data
    if _data is not None:
        return _data
    with _lock:
        if _data is not None:
            return _data
        try:
            z = np.load(config.GEO_FILE, allow_pickle=False)
            lat = np.radians(z["lat"].astype(np.float32))
            lon = np.radians(z["lon"].astype(np.float32))
            xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1).astype(np.float32)
            _data = {
                "xyz": xyz,
                "city": z["city"],
                "state": z["state"],
                "country": z["country"],
            }
            log.info("Loaded %d places for offline place names", len(xyz))
        except Exception as e:
            log.warning("Place names unavailable (%s)", e)
            _data = {}
    return _data


def lookup(lat: float, lon: float):
    """Returns (city, state, country) or (None, None, None)."""
    d = _load()
    if not d:
        return None, None, None
    la, lo = np.radians(lat), np.radians(lon)
    v = np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], dtype=np.float32)
    i = int(np.argmax(d["xyz"] @ v))
    # Too far from any known town (over ~60 km): only keep state/country.
    dist_km = float(np.arccos(np.clip(d["xyz"][i] @ v, -1, 1))) * 6371
    city = str(d["city"][i]) if dist_km < 60 else None
    state = str(d["state"][i]) or None
    country = str(d["country"][i]) or None
    if dist_km > 400:
        state = None
    return city, state, country
