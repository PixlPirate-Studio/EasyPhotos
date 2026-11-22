"""Smart search: finds photos by what's in them ("fireworks", "beach",
"birthday cake") without any tagging.

Uses CLIP (OpenAI ViT-B/32, the same model Immich uses by default). It was
trained on millions of pictures with captions, so it can turn both a photo
and a few words into a list of 512 numbers that land close together when
they mean the same thing. Each photo is looked at once in the background;
a search only has to turn the words into numbers and compare.
"""
import logging
import os
import threading
import time

import numpy as np
from PIL import Image

from . import config, db

log = logging.getLogger("easyphotos")

CLIP_DIR = os.path.join(config.MODELS_DIR, "clip")
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
SIZE = 224
CONTEXT = 77
SOT, EOT = 49406, 49407

_lock = threading.Lock()
_visual = None
_textual = None
_tok = None


def available() -> bool:
    return all(os.path.exists(os.path.join(CLIP_DIR, p)) for p in
               ("visual/model.onnx", "textual/model.onnx", "textual/tokenizer.json"))


def _session(path, threads):
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.intra_op_num_threads = max(1, threads)
    o.inter_op_num_threads = 1
    return ort.InferenceSession(path, o, providers=["CPUExecutionProvider"])


def visual(threads: int = 2):
    global _visual
    with _lock:
        if _visual is None:
            _visual = _session(os.path.join(CLIP_DIR, "visual/model.onnx"), threads)
        return _visual


def _text_parts():
    global _textual, _tok
    with _lock:
        if _textual is None:
            from tokenizers import Tokenizer
            _tok = Tokenizer.from_file(os.path.join(CLIP_DIR, "textual/tokenizer.json"))
            _textual = _session(os.path.join(CLIP_DIR, "textual/model.onnx"), 2)
        return _textual, _tok


def prepare(img: Image.Image) -> np.ndarray:
    """Shortest side to 224 (bicubic), centre crop 224x224, normalise. Returns 1x3x224x224."""
    img = img.convert("RGB")
    w, h = img.size
    s = SIZE / min(w, h)
    img = img.resize((max(SIZE, round(w * s)), max(SIZE, round(h * s))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - SIZE) // 2, (h - SIZE) // 2
    img = img.crop((left, top, left + SIZE, top + SIZE))
    a = (np.asarray(img, np.float32) / 255.0 - MEAN) / STD
    return a.transpose(2, 0, 1)[None].astype(np.float32)


def _norm(v):
    v = np.asarray(v, np.float32).reshape(-1)
    return v / (np.linalg.norm(v) + 1e-9)


def embed_image(img: Image.Image, threads: int = 2) -> np.ndarray:
    out = visual(threads).run(None, {"image": prepare(img)})[0]
    return _norm(out)


def embed_text(text: str) -> np.ndarray:
    sess, tok = _text_parts()
    ids = tok.encode(text.lower().strip()).ids
    if not ids or ids[0] != SOT:
        ids = [SOT] + ids
    if ids[-1] != EOT:
        ids = ids + [EOT]
    if len(ids) > CONTEXT:
        ids = ids[:CONTEXT - 1] + [EOT]
    arr = np.zeros((1, CONTEXT), np.int32)
    arr[0, :len(ids)] = ids
    return _norm(sess.run(None, {"text": arr})[0])


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, np.float16).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, np.float16).astype(np.float32)


# ---- search: all photo embeddings kept in memory (about 1 KB per photo)

class _Index:
    def __init__(self):
        self.ids = np.zeros(0, np.int64)
        self.mat = np.zeros((0, 512), np.float16)
        self.stamp = None
        self.loaded_at = 0.0
        self.lock = threading.Lock()

    def refresh(self):
        stamp = db.q1("SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS m FROM clip_embeddings")
        key = (stamp["n"], stamp["m"])
        if key == self.stamp and time.time() - self.loaded_at < 600:
            return
        with self.lock:
            rows = db.q("SELECT asset_id, emb FROM clip_embeddings")
            if rows:
                self.ids = np.fromiter((r["asset_id"] for r in rows), np.int64, len(rows))
                self.mat = np.frombuffer(b"".join(r["emb"] for r in rows), np.float16).reshape(len(rows), 512)
            else:
                self.ids = np.zeros(0, np.int64)
                self.mat = np.zeros((0, 512), np.float16)
            self.stamp = key
            self.loaded_at = time.time()


INDEX = _Index()


MIN_STANDOUT = 4.0  # how far above the average a photo must score to count as a match


def search(text: str, limit: int = 1000, matches_only: bool = True):
    """Best matching photos for the words, best first: [(asset_id, score)].

    Raw scores sit close together (0.20-0.30), so a fixed cut-off doesn't
    work. Instead, a photo counts as a match when it scores far above how
    this library scores on average for these words ("stands out"). Real
    matches stand out a lot; when nothing matches (no fireworks at all),
    nothing does."""
    INDEX.refresh()
    if not len(INDEX.ids):
        return []
    q = embed_text(text).astype(np.float32)
    sims = INDEX.mat.astype(np.float32) @ q
    order = np.argsort(-sims)[:limit]
    if matches_only and len(sims) >= 50:
        mean, sd = float(sims.mean()), float(sims.std()) or 1e-6
        order = [i for i in order if (sims[i] - mean) / sd >= MIN_STANDOUT and sims[i] >= 0.2]
    return [(int(INDEX.ids[i]), float(sims[i])) for i in order]
