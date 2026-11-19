"""Face detection and recognition.

Uses the same InsightFace "buffalo_l" models that Immich uses:
  - SCRFD (detection/model.onnx) finds faces and 5 landmarks
  - ArcFace (recognition/model.onnx) turns each face into a 512-number "fingerprint"
Everything runs on the CPU on your own server.
"""
import logging
import threading

import cv2
import numpy as np

from . import config

log = logging.getLogger("easyphotos.faces")

ARCFACE_DST = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

DET_SIZE = 640


def _similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Umeyama estimate of a 2x3 similarity transform mapping src -> dst."""
    num = src.shape[0]
    src_mean, dst_mean = src.mean(0), dst.mean(0)
    src_d, dst_d = src - src_mean, dst - dst_mean
    A = dst_d.T @ src_d / num
    d = np.ones(2)
    if np.linalg.det(A) < 0:
        d[1] = -1
    U, S, Vt = np.linalg.svd(A)
    R = U @ np.diag(d) @ Vt
    var = src_d.var(0).sum()
    scale = (S * d).sum() / var if var > 0 else 1.0
    t = dst_mean - scale * (R @ src_mean)
    M = np.zeros((2, 3), dtype=np.float32)
    M[:, :2] = scale * R
    M[:, 2] = t
    return M


def _nms(boxes: np.ndarray, scores: np.ndarray, thresh: float = 0.4):
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


class FaceEngine:
    def __init__(self, threads: int = 4):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 3
        det_path = config.MODELS_DIR / "detection" / "model.onnx"
        rec_path = config.MODELS_DIR / "recognition" / "model.onnx"
        if not det_path.exists() or not rec_path.exists():
            raise FileNotFoundError(f"Face models not found in {config.MODELS_DIR}")
        providers = ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(str(det_path), sess_options=opts, providers=providers)
        self.rec = ort.InferenceSession(str(rec_path), sess_options=opts, providers=providers)
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name
        self.det_outputs = [o.name for o in self.det.get_outputs()]
        self.threads = threads
        self._anchor_cache = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ detection
    def _anchors(self, h, w, stride, num_anchors=2):
        key = (h, w, stride)
        if key not in self._anchor_cache:
            ac = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            ac = (ac * stride).reshape((-1, 2))
            ac = np.stack([ac] * num_anchors, axis=1).reshape((-1, 2))
            self._anchor_cache[key] = ac
        return self._anchor_cache[key]

    def detect(self, rgb: np.ndarray, min_score: float = 0.7, min_size: int = 40):
        """rgb: HxWx3 uint8. Returns list of (box[x1,y1,x2,y2], score, kps[5,2]) in image pixels."""
        ih, iw = rgb.shape[:2]
        scale = DET_SIZE / max(ih, iw)
        nw, nh = int(round(iw * scale)), int(round(ih * scale))
        resized = cv2.resize(rgb, (nw, nh))
        canvas = np.zeros((DET_SIZE, DET_SIZE, 3), dtype=np.uint8)
        canvas[:nh, :nw] = resized
        blob = ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        with self._lock:
            outs = self.det.run(self.det_outputs, {self.det_input: blob})
        if outs[0].ndim == 3:  # batched model output
            outs = [o[0] for o in outs]
        fmc = 3
        strides = (8, 16, 32)
        all_scores, all_boxes, all_kps = [], [], []
        for idx, stride in enumerate(strides):
            scores = outs[idx].reshape(-1)
            bbox = outs[idx + fmc].reshape(-1, 4) * stride
            kps = outs[idx + fmc * 2].reshape(-1, 10) * stride
            h, w = DET_SIZE // stride, DET_SIZE // stride
            anchors = self._anchors(h, w, stride)
            pos = np.where(scores >= min_score)[0]
            if pos.size == 0:
                continue
            ac = anchors[pos]
            b = bbox[pos]
            boxes = np.stack([ac[:, 0] - b[:, 0], ac[:, 1] - b[:, 1], ac[:, 0] + b[:, 2], ac[:, 1] + b[:, 3]], axis=1)
            k = kps[pos].reshape(-1, 5, 2) + ac[:, None, :]
            all_scores.append(scores[pos])
            all_boxes.append(boxes)
            all_kps.append(k)
        if not all_scores:
            return []
        scores = np.concatenate(all_scores)
        boxes = np.concatenate(all_boxes) / scale
        kps = np.concatenate(all_kps) / scale
        keep = _nms(boxes, scores, 0.4)
        results = []
        for i in keep:
            x1, y1, x2, y2 = boxes[i]
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(iw), x2), min(float(ih), y2)
            if min(x2 - x1, y2 - y1) < min_size:
                continue
            results.append((np.array([x1, y1, x2, y2], dtype=np.float32), float(scores[i]), kps[i]))
        return results

    # ------------------------------------------------------------ recognition
    def embed(self, rgb: np.ndarray, kps: np.ndarray) -> np.ndarray:
        M = _similarity_transform(kps.astype(np.float32), ARCFACE_DST)
        face = cv2.warpAffine(rgb, M, (112, 112), borderValue=0.0)
        return self._embed_aligned(face)

    def embed_box(self, rgb: np.ndarray, box) -> tuple[np.ndarray, np.ndarray | None]:
        """Embedding for a face the user drew by hand. Tries to find landmarks
        inside the box first; falls back to a plain square crop."""
        ih, iw = rgb.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        pad = 0.6
        cx1, cy1 = int(max(0, x1 - bw * pad)), int(max(0, y1 - bh * pad))
        cx2, cy2 = int(min(iw, x2 + bw * pad)), int(min(ih, y2 + bh * pad))
        crop = rgb[cy1:cy2, cx1:cx2]
        if crop.size and min(crop.shape[:2]) >= 24:
            found = self.detect(np.ascontiguousarray(crop), min_score=0.3, min_size=8)
            if found:
                # pick the detection closest to the drawn box centre
                cx, cy = (x1 + x2) / 2 - cx1, (y1 + y2) / 2 - cy1
                best = min(found, key=lambda f: (((f[0][0] + f[0][2]) / 2 - cx) ** 2 + ((f[0][1] + f[0][3]) / 2 - cy) ** 2))
                kps = best[2] + np.array([cx1, cy1], dtype=np.float32)
                return self.embed(rgb, kps), kps
        side = int(max(bw, bh))
        mx, my = int((x1 + x2) / 2), int((y1 + y2) / 2)
        sx1, sy1 = max(0, mx - side // 2), max(0, my - side // 2)
        sq = rgb[sy1:sy1 + side, sx1:sx1 + side]
        if sq.size == 0:
            sq = rgb
        face = cv2.resize(np.ascontiguousarray(sq), (112, 112))
        return self._embed_aligned(face), None

    def _embed_aligned(self, face: np.ndarray) -> np.ndarray:
        blob = ((face.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        with self._lock:
            out = self.rec.run(None, {self.rec_input: blob})[0][0]
        n = np.linalg.norm(out)
        return (out / n if n > 0 else out).astype(np.float32)


_engine = None
_engine_threads = None
_engine_lock = threading.Lock()


def get_engine(threads: int = 4) -> FaceEngine:
    global _engine, _engine_threads
    with _engine_lock:
        if _engine is None or _engine_threads != threads:
            _engine = None
            _engine = FaceEngine(threads)
            _engine_threads = threads
        return _engine


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
