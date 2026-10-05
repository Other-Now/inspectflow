"""Image preprocessing shared by training and the edge worker.

Training and inference must use the exact same function — a mismatch here
(different resize interpolation, different normalisation) is a silent accuracy
loss that no test on the model alone would catch.
"""
import cv2
import numpy as np

CLASSES = ["crazing", "inclusion", "patches", "pitted_surface", "rolled-in_scale", "scratches"]
SIZE = 128


def load_gray(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read image: {path}")
    return img


def to_tensor(gray: np.ndarray) -> np.ndarray:
    """uint8 HxW -> float32 1x1xSIZExSIZE in [-1, 1]."""
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    x = cv2.resize(gray, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    x = x.astype(np.float32) / 255.0
    x = (x - 0.5) / 0.5
    return x[None, None, :, :]


def thumbnail_jpeg(gray: np.ndarray, size: int = 96, quality: int = 80) -> bytes:
    t = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", t, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def label_from_filename(name: str) -> int:
    stem = name.rsplit(".", 1)[0]
    cls = stem.rsplit("_", 1)[0]
    return CLASSES.index(cls)
