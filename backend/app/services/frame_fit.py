"""相框內部偵測與肖像配置（純 numpy／cv2，不需 GPU）。

背景圖若畫了一個華麗相框，找出框內的區域（供合成時把角色裁在框內），
並算出「肖像」的位置：角色頭到腰約佔框內高度，放在框的中央。
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

# 種子點（相對座標 x, y）：相框通常在畫面中央偏下
DEFAULT_SEED: tuple[float, float] = (0.5, 0.55)

INK_GRAY_MAX = 160  # 灰階低於此值視為線條
DILATE_PX_AT_832 = 5  # 線條膨脹核（寬 832 時的邊長），用來封閉細小缺口
MIN_DILATE_PX = 3
SEED_SEARCH_FRACTION = 0.03  # 種子落在線上時，在寬度的這個比例內找最近的空白像素
MIN_AREA_FRACTION = 0.05  # 內部面積占整張圖的比例範圍；超出視為不是相框
MAX_AREA_FRACTION = 0.75
MAX_EDGES_TOUCHED = 1  # 區域最多碰到幾條圖片邊（拱門只碰下緣）

# 肖像：角色高度 = 內部高度 × 0.92／0.62（角色可見部分約 62%，其餘被框外裁掉）；頂端留 8% 內部高度
PORTRAIT_TOP_MARGIN = 0.08
PORTRAIT_VISIBLE_FRACTION = 0.62
PORTRAIT_HEIGHT_FACTOR = 0.92


def _fill_holes(region: np.ndarray) -> np.ndarray:
    """區域內被完全包住的東西（例如框內的裝飾）也算內部。"""
    h, w = region.shape
    outside = np.pad((~region).astype(np.uint8), 1, constant_values=1)  # 外圈補 1，從角落填滿外部
    flood = outside.copy()
    cv2.floodFill(flood, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 2)
    # 從外面碰得到的（== 2）是外部；其餘（區域本身 + 洞）是內部
    return (flood != 2)[1:-1, 1:-1]


def find_interior(background: Image.Image, seed: tuple[float, float]) -> np.ndarray | None:
    """回傳 H×W 布林遮罩（True = 相框內部、洞已填滿）；沒有相框則回傳 None。"""
    gray = np.array(background.convert("L"))
    h, w = gray.shape
    ink = (gray < INK_GRAY_MAX).astype(np.uint8)
    k = max(MIN_DILATE_PX, round(DILATE_PX_AT_832 * w / 832))
    ink = cv2.dilate(ink, np.ones((k, k), np.uint8))
    mask = _interior_from_ink(ink, seed)
    if mask is not None:
        return mask
    # 拱門的柱腳若停在紙邊之上，內部會從柱腳下漏到框外：在最低的線條處補一條虛擬地板再試一次
    rows = np.nonzero(ink.any(axis=1))[0]
    if rows.size == 0 or rows.max() >= h - k:  # 線條已到紙邊：不是柱腳懸空的拱門
        return None
    floored = ink.copy()
    floored[max(0, int(rows.max()) - k + 1) : int(rows.max()) + 1, :] = 1
    return _interior_from_ink(floored, seed)


def _interior_from_ink(ink: np.ndarray, seed: tuple[float, float]) -> np.ndarray | None:
    """（已膨脹的）線條遮罩 → 種子所在空白區域通過規則後的內部遮罩。"""
    h, w = ink.shape
    free = (ink == 0).astype(np.uint8)
    _, labels = cv2.connectedComponents(free, connectivity=4)

    sx = min(w - 1, max(0, int(seed[0] * w)))
    sy = min(h - 1, max(0, int(seed[1] * h)))
    if labels[sy, sx]:
        candidates = [int(labels[sy, sx])]
    else:  # 種子在線上：依距離由近到遠，試附近的空白區域（線的兩側都可能是候選）
        radius = int(SEED_SEARCH_FRACTION * w)
        y0, y1 = max(0, sy - radius), min(h, sy + radius + 1)
        x0, x1 = max(0, sx - radius), min(w, sx + radius + 1)
        ys, xs = np.nonzero(labels[y0:y1, x0:x1])
        d = (ys + y0 - sy) ** 2 + (xs + x0 - sx) ** 2
        near = labels[y0:y1, x0:x1][ys, xs][d <= radius**2]
        order = np.argsort(d[d <= radius**2], kind="stable")
        candidates = list(dict.fromkeys(int(v) for v in near[order]))

    for label in candidates:
        mask = _validate_region(labels == label)
        if mask is not None:
            return mask
    return None


def _validate_region(region: np.ndarray) -> np.ndarray | None:
    """檢查邊緣與面積規則，通過則回傳填洞後的遮罩。"""
    edges = sum(bool(e.any()) for e in (region[0], region[-1], region[:, 0], region[:, -1]))
    if edges > MAX_EDGES_TOUCHED:
        return None
    mask = _fill_holes(region)
    if not MIN_AREA_FRACTION <= mask.mean() <= MAX_AREA_FRACTION:
        return None
    return mask


def fit_portrait(
    interior: np.ndarray, cutout_size: tuple[int, int]
) -> tuple[float, float, float]:
    """回傳 (cx, cy, height)，皆相對背景圖（height 可能大於 1）。

    cutout_size（寬, 高）目前不參與計算：角色高度只由框內高度決定，保留給日後依長寬比調整。
    """
    h, w = interior.shape
    ys, xs = np.nonzero(interior)
    top, bottom = int(ys.min()), int(ys.max()) + 1
    left, right = int(xs.min()), int(xs.max()) + 1
    inner_h = bottom - top
    char_h = inner_h * PORTRAIT_HEIGHT_FACTOR / PORTRAIT_VISIBLE_FRACTION
    char_top = top + PORTRAIT_TOP_MARGIN * inner_h
    cx = (left + right) / 2 / w
    cy = (char_top + char_h / 2) / h
    return cx, cy, char_h / h


def interior_png(mask: np.ndarray) -> bytes:
    """L 模式 PNG，255 = 內部。"""
    buf = io.BytesIO()
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(buf, "PNG")
    return buf.getvalue()
