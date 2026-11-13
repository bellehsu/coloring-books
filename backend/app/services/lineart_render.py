"""線稿渲染：彩色圖 -> xdog 線稿 -> 後處理，以及使用者修改筆畫圖層。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

import cv2
import numpy as np

from app.services import lineart_params, lineart_post
from app.services.imageio import ink_to_png  # noqa: F401  (re-export：實驗腳本沿用舊匯入路徑)
from app.services.lineart_extract import extract_xdog, fill_outlines

RENDER_VERSION = 1  # 演算法改變時 +1，讓快取失效
LINE_WIDTH_MIN, LINE_WIDTH_MAX, LINE_WIDTH_DEFAULT = 8, 16, 11


class LineDetail(str, Enum):
    SIMPLE = "simple"
    STANDARD = "standard"
    DETAILED = "detailed"


DETAIL_SIGMA = {LineDetail.SIMPLE: 1.5, LineDetail.STANDARD: 1.2, LineDetail.DETAILED: 0.8}


@dataclass(frozen=True)
class Stroke:
    op: Literal["draw", "erase"]
    width_px: float
    points: tuple[tuple[float, float], ...]


def render_params() -> dict:
    """決定線稿像素的所有固定參數（含各細節的 sigma）：lineart_key 把它整份放進雜湊。"""
    return {**lineart_params.as_dict(), "sigma": {d.value: s for d, s in DETAIL_SIGMA.items()}}


def _xdog_gray(color: np.ndarray, detail: LineDetail) -> np.ndarray:
    return extract_xdog(color, DETAIL_SIGMA[LineDetail(detail)])


def render_lineart(color: np.ndarray, content_px: tuple[int, int], detail: LineDetail,
                   line_width_px: int, *, outline_fill: bool = False) -> np.ndarray:
    """產品的底稿：彩色圖 → xdog →（outline_fill：粗筆畫填實成單線）→ 後處理，回傳 ink（True＝黑）。不算指標。"""
    gray = _xdog_gray(color, detail)
    if outline_fill:
        gray = fill_outlines(color, gray)
    return lineart_post.render_ink(gray, content_px, line_width_px)


def render_base(color: np.ndarray, content_px: tuple[int, int], detail: LineDetail,
                line_width_px: int) -> tuple[np.ndarray, dict]:
    """同 render_lineart，另外回傳 lineart_post.process 的品質指標（實驗腳本用；產品用 render_lineart）。"""
    w, h = content_px
    res = lineart_post.process(_xdog_gray(color, detail), {
        "target_width": w, "target_height": h, "line_width_px": line_width_px,
        "fit": lineart_params.FIT, "interpolation": lineart_params.INTERPOLATION,
        "binarize": lineart_params.BINARIZE, "min_fragment_area": lineart_params.MIN_FRAGMENT_AREA,
        "close_kernel": lineart_params.CLOSE_KERNEL, "close_iterations": lineart_params.CLOSE_ITERATIONS,
    })
    return res.ink, res.metrics


def stroke_patch(s: Stroke, w: int, h: int) -> tuple[int, int, np.ndarray] | None:
    """一筆畫在畫布（w×h）上的像素，只配置筆畫外框（外擴一個筆寬）大的遮罩：回傳 (x0, y0, bool 子陣列)，
    子陣列對應畫布 [y0:y0+rows, x0:x0+cols]；沒有點或外框完全在畫布外時 None。結果與整頁遮罩版相同。"""
    if not s.points:
        return None
    pts = np.array([[round(x * (w - 1)), round(y * (h - 1))] for x, y in s.points], np.int32)
    t = max(1, round(s.width_px))
    pad = t + 2
    x0, y0 = max(0, int(pts[:, 0].min()) - pad), max(0, int(pts[:, 1].min()) - pad)
    x1, y1 = min(w, int(pts[:, 0].max()) + pad + 1), min(h, int(pts[:, 1].max()) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    local = pts - np.array([x0, y0], np.int32)
    if len(local) > 1:
        cv2.polylines(mask, [local.reshape(-1, 1, 2)], False, 1, t, cv2.LINE_8)
    r = max(0, t // 2)
    for x, y in local:
        cv2.circle(mask, (int(x), int(y)), r, 1, -1)
    return x0, y0, mask.astype(bool)


def apply_strokes(ink: np.ndarray, strokes: Sequence[Stroke]) -> np.ndarray:
    out = ink.astype(bool).copy()
    h, w = out.shape
    for s in strokes:
        patch = stroke_patch(s, w, h)
        if patch is None:
            continue
        x0, y0, m = patch
        out[y0:y0 + m.shape[0], x0:x0 + m.shape[1]][m] = s.op == "draw"
    return out
