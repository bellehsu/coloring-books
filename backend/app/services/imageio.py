"""影像編解碼的共用小工具：透明壓平、1-bit 線稿 PNG 的編碼／解碼、圓形核。

產品裡每一處壓平透明度、讀寫 1-bit 線稿 PNG 都走這裡，改一處就全部一起改。
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

LINEART_DPI = 300


def flatten_on_white(img: Image.Image) -> Image.Image:
    """轉成 RGB；有透明度（RGBA／LA／PA／帶 transparency 的 P）的圖先鋪在白底上（避免透明處變黑）。"""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


def ink_to_bilevel(ink: np.ndarray) -> Image.Image:
    """ink（True＝黑）→ 1-bit 圖（白底黑線，不抖動）。"""
    arr = np.where(ink, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="L").convert("1", dither=Image.Dither.NONE)


def ink_to_png(ink: np.ndarray, dpi: int = LINEART_DPI) -> bytes:
    buf = io.BytesIO()
    ink_to_bilevel(ink).save(buf, format="PNG", dpi=(dpi, dpi))
    return buf.getvalue()


def png_to_ink(data: bytes) -> np.ndarray:
    """線稿 PNG → ink 陣列（True＝黑）。"""
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("L")) < 128


def disk_kernel(diameter: int) -> np.ndarray:
    """直徑 diameter（至少 1）像素的圓形結構元素。"""
    d = max(1, int(diameter))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))
