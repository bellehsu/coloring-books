"""線稿擷取方法實驗工具（Phase 1 Route C）：none／luma／xdog／color_edges／hybrid 五種方法與參數驗證。

產品只用 xdog（app.services.lineart_extract.extract_xdog）；其餘方法只給 experiments/run_matrix 等實驗腳本用。

從彩色插畫擷取線稿（Route C：先生成彩色角色圖，再擷取線條）。

輸出一律是灰階 uint8（白底黑線，0/255），接著交給 lineart_post.process() 做放大、清理、統一線寬。
全部是確定性的影像處理，不需要額外模型。

方法：
  none         不擷取，直接轉灰階（對照組：等同把彩色圖直接丟進後處理）。
  luma         亮度門檻：灰階 < threshold 視為線。適合「有深色外框的平塗插畫」——
               外框幾乎是黑的，填色（含中間調陰影）亮度較高，會被排除。
  xdog         eXtended Difference of Gaussians（Winnemöller 2012）。對亮度變化敏感、線條連續，
               會保留眼睛等細節，但也會把明顯的陰影邊界畫成線。
  color_edges  雙邊濾波後在 Lab 三個通道各做 Canny 再合併。抓色塊交界，
               但每條外框兩側都會各出一條邊，容易變成雙線（主要當對照組）。
  hybrid       以 luma 為主，修補 luma 的兩個弱點（Route C2 實驗發現）：
               1. 深色區域（夜空、深色衣物）：原圖以 dark_sigma 大範圍模糊後亮度 < dark_threshold 的地方，
                  luma 會整片變黑；這些區域改用 xdog 的結果。
               2. 淺色或細的線（淺藍家具邊線）：luma 抓不到。從 xdog 補回「夠長（外接框長邊 >= min_line_length）、
                  附近（near_px）沒有 luma 線、而且位在大片空白區域（luma 白色區塊面積 >= open_space_frac × 全圖）」
                  的線段。已被 luma 外框封閉的小區域（例如衣服）不補，避免把條紋畫進來。
               長度類參數以生成解析度（約 832×1216）為準。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from app.services.imageio import disk_kernel
from app.services.lineart_extract import _rgb, _to_image, _xdog

# 每個方法的參數預設值（同時也是允許的參數白名單）
METHODS: dict[str, dict[str, float | int]] = {
    "none": {},
    "luma": {"threshold": 90, "median_ksize": 0},
    "xdog": {"sigma": 0.8, "k": 1.6, "p": 20.0, "eps": 0.02, "phi": 10.0},
    "color_edges": {"bilateral_d": 9, "sigma_color": 40.0, "sigma_space": 7.0, "canny_low": 40, "canny_high": 110},
}
METHODS["hybrid"] = {
    **METHODS["luma"], **METHODS["xdog"],
    "dark_sigma": 12.0, "dark_threshold": 120, "min_line_length": 120, "near_px": 7, "open_space_frac": 0.05,
}

_INT_PARAMS = {"threshold", "median_ksize", "bilateral_d", "canny_low", "canny_high", "dark_threshold",
               "min_line_length", "near_px"}


@dataclass
class ExtractSpec:
    method: str = "none"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ExtractSpec":
        d = dict(d or {})
        method = d.pop("method", "none")
        if method not in METHODS:
            raise ValueError(f"擷取 method 必須是 {list(METHODS)} 之一，收到 {method!r}")
        unknown = set(d) - set(METHODS[method])
        if unknown:
            raise ValueError(f"未知的擷取參數（{method}）：{', '.join(sorted(unknown))}")
        spec = cls(method, {**METHODS[method], **d})
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method, **self.params}

    def validate(self) -> None:
        p = self.params
        for k, v in p.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"擷取參數 {k} 必須是數字，收到 {v!r}")
            if k in _INT_PARAMS and int(v) != v:
                raise ValueError(f"擷取參數 {k} 必須是整數，收到 {v!r}")
        if self.method in ("luma", "hybrid"):
            if not 0 < p["threshold"] <= 255:
                raise ValueError("threshold 必須在 1–255")
            if p["median_ksize"] < 0 or (p["median_ksize"] and p["median_ksize"] % 2 == 0):
                raise ValueError("median_ksize 必須是 0（不做）或正奇數")
        if self.method in ("xdog", "hybrid"):
            for k in ("sigma", "k", "phi"):
                if p[k] <= 0:
                    raise ValueError(f"{k} 必須 > 0")
            if p["k"] <= 1:
                raise ValueError("k 必須 > 1")
        if self.method == "hybrid":
            if p["dark_sigma"] <= 0:
                raise ValueError("dark_sigma 必須 > 0")
            if not 0 <= p["dark_threshold"] <= 255:
                raise ValueError("dark_threshold 必須在 0–255")
            for k in ("min_line_length", "near_px"):
                if p[k] < 0:
                    raise ValueError(f"{k} 不可為負數")
            if not 0 < p["open_space_frac"] <= 1:
                raise ValueError("open_space_frac 必須在 (0, 1]")
        elif self.method == "color_edges":
            if p["bilateral_d"] < 0:
                raise ValueError("bilateral_d 不可為負數")
            if not 0 <= p["canny_low"] < p["canny_high"]:
                raise ValueError("canny_low 必須 >= 0 且小於 canny_high")


def _luma(rgb: np.ndarray, threshold: int, median_ksize: int) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if median_ksize:
        gray = cv2.medianBlur(gray, int(median_ksize))
    return gray < threshold


def _color_edges(rgb: np.ndarray, bilateral_d: int, sigma_color: float, sigma_space: float,
                 canny_low: int, canny_high: int) -> np.ndarray:
    smooth = cv2.bilateralFilter(rgb, int(bilateral_d), sigma_color, sigma_space) if bilateral_d else rgb
    lab = cv2.cvtColor(smooth, cv2.COLOR_RGB2LAB)
    edges = np.zeros(rgb.shape[:2], bool)
    for c in range(3):
        edges |= cv2.Canny(np.ascontiguousarray(lab[..., c]), int(canny_low), int(canny_high)) > 0
    return edges


def _hybrid(rgb: np.ndarray, *, threshold: int, median_ksize: int, sigma: float, k: float, p: float, eps: float,
            phi: float, dark_sigma: float, dark_threshold: int, min_line_length: int, near_px: int,
            open_space_frac: float) -> np.ndarray:
    luma = _luma(rgb, threshold, median_ksize)
    xd = _xdog(rgb, sigma, k, p, eps, phi)

    # 1. 深色區域改用 xdog
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    dark = cv2.GaussianBlur(gray, (0, 0), dark_sigma) < dark_threshold
    dark = cv2.dilate(dark.astype(np.uint8), disk_kernel(2 * dark_sigma + 1)) > 0
    out = (luma & ~dark) | (xd & dark)

    # 2. 在大片空白區域補回 xdog 的長線
    n, labels, stats, _ = cv2.connectedComponentsWithStats(xd.astype(np.uint8), connectivity=8)
    long_ = np.zeros(n, bool)
    long_[1:] = np.maximum(stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]) >= min_line_length
    near_luma = cv2.dilate(luma.astype(np.uint8), disk_kernel(near_px)) > 0 if near_px else luma
    closed = cv2.morphologyEx(luma.astype(np.uint8), cv2.MORPH_CLOSE, disk_kernel(5)) > 0
    m, wlabels, wstats, _ = cv2.connectedComponentsWithStats((~closed).astype(np.uint8), connectivity=4)
    big = np.zeros(m, bool)
    big[1:] = wstats[1:, cv2.CC_STAT_AREA] >= open_space_frac * luma.size
    add = long_[labels] & ~near_luma & ~dark & big[wlabels]
    return out | add


def extract(img: np.ndarray, spec: ExtractSpec | dict[str, Any] | None) -> np.ndarray:
    """img：RGB / RGBA / 灰階 uint8。回傳灰階 uint8（白底黑線）；method=none 時回傳原灰階。"""
    s = spec if isinstance(spec, ExtractSpec) else ExtractSpec.from_dict(spec)
    rgb = _rgb(img)
    if s.method == "none":
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    fn = {"luma": _luma, "xdog": _xdog, "color_edges": _color_edges, "hybrid": _hybrid}[s.method]
    return _to_image(fn(rgb, **s.params))
