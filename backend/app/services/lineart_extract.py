"""從彩色插畫擷取線稿：xdog（eXtended Difference of Gaussians，Winnemöller 2012）。

產品的唯一擷取方法：灰階 → 兩個不同 sigma 的高斯模糊相減 → tanh 軟門檻。對亮度變化敏感、線條連續，
會保留眼睛等細節，但也會把明顯的陰影邊界畫成線。k／p／eps／phi 是固定值（lineart_params），只有 sigma
隨「線條細節」調整。輸出是灰階 uint8（白底黑線，0/255），接著交給 lineart_post.render_ink 放大、清理、統一線寬。
全部是確定性的影像處理，不需要額外模型。

其他方法（luma、color_edges、hybrid 等 Phase 1 實驗）在 experiments/lineart_methods.py。
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.services import lineart_params as lp
from app.services.imageio import flatten_on_white


def _rgb(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    if img.shape[2] == 4:
        return np.asarray(flatten_on_white(Image.fromarray(img, "RGBA")))
    return img[..., :3]


def _to_image(ink: np.ndarray) -> np.ndarray:
    return np.where(ink, 0, 255).astype(np.uint8)


def _xdog(rgb: np.ndarray, sigma: float, k: float, p: float, eps: float, phi: float) -> np.ndarray:
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    a = cv2.GaussianBlur(g, (0, 0), sigma)
    b = cv2.GaussianBlur(g, (0, 0), sigma * k)
    d = (1 + p) * a - p * b
    e = np.where(d >= eps, 1.0, 1.0 + np.tanh(phi * (d - eps)))
    return e < 0.5


def extract_xdog(img: np.ndarray, sigma: float) -> np.ndarray:
    """img：RGB／RGBA／灰階 uint8。回傳灰階 uint8（白底黑線）。"""
    return _to_image(_xdog(_rgb(img), sigma, lp.XDOG_K, lp.XDOG_P, lp.XDOG_EPS, lp.XDOG_PHI))
