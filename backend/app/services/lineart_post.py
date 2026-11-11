"""線稿後處理（確定性、可測試）。

流程：放大到目標尺寸 → 灰階 → 二值化 → 移除小碎片 → 形態學閉運算封閉缺口 → 統一線寬 → 1-bit PNG。
每一步都由 LineartParams 參數化；process() 同時回傳品質指標。

內部以布林陣列 ink（True = 黑線）表示二值圖。
預設值以 A4 300 DPI（2480 × 3508 px）、線寬約 11 px 為準；面積類參數單位皆為目標解析度下的像素。

指標（compute_metrics）：
  gray_pixel_ratio    原圖灰階中「既不是黑也不是白」的像素比例（gray_low < v < gray_high）。
                      在原始解析度計算，避免放大內插造成的邊緣灰階。越低越像乾淨線稿。
  fragment_count      二值化後、面積 < min_fragment_area 的黑色連通元件數（雜點、斷筆）。
  black_fill_ratio    移除碎片後，比線寬明顯更粗的實心黑塊面積比例（以 2×線寬+1 的圓形核做 opening）。
                      著色本應接近 0。
  region_count        輸出圖中白色連通區域總數（4-連通）。
  closed_region_count 其中不碰到圖片邊界的區域數 —— 真正被線條封閉、可以塗色的區域。
  tiny_region_count   面積 < min_region_area 的白色區域數 —— 太小、小孩塗不進去。
  black_pixel_ratio   輸出圖黑色像素比例。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, BinaryIO

import cv2
import numpy as np
from PIL import Image
from skimage.morphology import skeletonize

A4_300DPI = (2480, 3508)

_INTERP = {
    "nearest": cv2.INTER_NEAREST,
    "linear": cv2.INTER_LINEAR,
    "cubic": cv2.INTER_CUBIC,
    "lanczos": cv2.INTER_LANCZOS4,
    "area": cv2.INTER_AREA,
}


@dataclass
class LineartParams:
    # 1. 放大
    target_width: int = A4_300DPI[0]
    target_height: int = A4_300DPI[1]
    fit: str = "contain"  # contain：等比縮放後白邊補滿；cover：等比放大後置中裁切；stretch：直接拉伸
    interpolation: str = "lanczos"  # 縮小時一律改用 area
    # 3. 二值化
    binarize: str = "otsu"  # otsu | fixed | adaptive
    threshold: int = 160  # fixed：灰階 < threshold 視為黑
    adaptive_block_size: int = 51
    adaptive_c: int = 10
    # 4. 移除小碎片
    min_fragment_area: int = 64
    # 5. 閉運算
    close_kernel: int = 7  # 0 = 不做
    close_iterations: int = 1
    # 6. 統一線寬
    line_width_px: int = 11  # 0 = 不統一（保留原線寬）
    # 指標
    gray_low: int = 32
    gray_high: int = 223
    min_region_area: int = 3000
    dpi: int = 300

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LineartParams":
        d = d or {}
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"未知的後處理參數：{', '.join(sorted(unknown))}")
        p = cls(**d)
        p.validate()
        return p

    def validate(self) -> None:
        if self.target_width <= 0 or self.target_height <= 0:
            raise ValueError("target_width/target_height 必須 > 0")
        if self.fit not in ("contain", "cover", "stretch"):
            raise ValueError(f"fit 必須是 contain/cover/stretch，收到 {self.fit}")
        if self.interpolation not in _INTERP:
            raise ValueError(f"interpolation 必須是 {list(_INTERP)} 之一")
        if self.binarize not in ("otsu", "fixed", "adaptive"):
            raise ValueError(f"binarize 必須是 otsu/fixed/adaptive，收到 {self.binarize}")
        if self.adaptive_block_size < 3 or self.adaptive_block_size % 2 == 0:
            raise ValueError("adaptive_block_size 必須是 >= 3 的奇數")
        for name in ("min_fragment_area", "close_kernel", "close_iterations", "line_width_px", "min_region_area"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不可為負數")


@dataclass
class LineartResult:
    ink: np.ndarray  # bool，True = 黑
    metrics: dict[str, float | int]
    params: LineartParams
    steps: dict[str, np.ndarray] = field(default_factory=dict)  # keep_steps=True 時保留中間結果


# ---- 各步驟 -------------------------------------------------------------------

def load_image(path: str | Path | BinaryIO) -> np.ndarray:
    """讀成 RGB uint8；有透明度時合成到白底。path 可以是檔案路徑或 file-like（例如 io.BytesIO）。"""
    with Image.open(path) as im:
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        return np.asarray(im.convert("RGB"))


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img.astype(np.uint8)
    if img.shape[2] == 4:
        rgb = img[..., :3].astype(np.float32)
        a = img[..., 3:4].astype(np.float32) / 255.0
        img = (rgb * a + 255.0 * (1 - a)).astype(np.uint8)
    return cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2GRAY)


def resize_to_target(img: np.ndarray, p: LineartParams) -> np.ndarray:
    h, w = img.shape[:2]
    tw, th = p.target_width, p.target_height
    if p.fit == "stretch":
        scale_x, scale_y = tw / w, th / h
        interp = _INTERP["area"] if scale_x * scale_y < 1 else _INTERP[p.interpolation]
        return cv2.resize(img, (tw, th), interpolation=interp)
    scale = min(tw / w, th / h) if p.fit == "contain" else max(tw / w, th / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    interp = _INTERP["area"] if scale < 1 else _INTERP[p.interpolation]
    resized = cv2.resize(img, (nw, nh), interpolation=interp)
    if p.fit == "contain":
        canvas = np.full((th, tw) + img.shape[2:], 255, dtype=img.dtype)
        y0, x0 = (th - nh) // 2, (tw - nw) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = resized
        return canvas
    y0, x0 = (nh - th) // 2, (nw - tw) // 2
    return resized[y0:y0 + th, x0:x0 + tw]


def binarize(gray: np.ndarray, p: LineartParams) -> np.ndarray:
    if p.binarize == "fixed":
        return gray < p.threshold
    if p.binarize == "otsu":
        if gray.min() == gray.max():  # 全白或全黑時 Otsu 無意義
            return gray < 128
        _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return out > 0
    out = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                p.adaptive_block_size, p.adaptive_c)
    return out > 0


def _components(mask: np.ndarray, connectivity: int) -> tuple[int, np.ndarray, np.ndarray]:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    return n, labels, stats


def count_small_components(ink: np.ndarray, min_area: int) -> int:
    n, _, stats = _components(ink, 8)
    return int(np.sum(stats[1:, cv2.CC_STAT_AREA] < min_area))


def remove_small_fragments(ink: np.ndarray, min_area: int) -> np.ndarray:
    if min_area <= 0:
        return ink.copy()
    n, labels, stats = _components(ink, 8)
    keep = np.zeros(n, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[labels]


def _disk(diameter: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))


def close_gaps(ink: np.ndarray, kernel: int, iterations: int = 1) -> np.ndarray:
    if kernel <= 1 or iterations <= 0:
        return ink.copy()
    out = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, _disk(kernel), iterations=iterations)
    return out > 0


def unify_line_width(ink: np.ndarray, width: int) -> np.ndarray:
    """骨架化後以圓形核膨脹到固定線寬。width <= 0 時不處理。"""
    if width <= 0 or not ink.any():
        return ink.copy()
    skel = skeletonize(ink)
    if width == 1:
        return skel
    return cv2.dilate(skel.astype(np.uint8), _disk(width)) > 0


def save_1bit_png(ink: np.ndarray, path: str | Path, dpi: int = 300) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.where(ink, 0, 255).astype(np.uint8)
    Image.fromarray(arr, mode="L").convert("1", dither=Image.Dither.NONE).save(path, dpi=(dpi, dpi), optimize=True)
    return path


# ---- 指標 -----------------------------------------------------------------------

def gray_pixel_ratio(gray: np.ndarray, low: int, high: int) -> float:
    return float(np.mean((gray > low) & (gray < high)))


def black_fill_ratio(ink: np.ndarray, line_width_px: int) -> float:
    k = max(3, 2 * max(line_width_px, 1) + 1)
    opened = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, _disk(k))
    return float(opened.mean())


def region_stats(ink: np.ndarray, min_region_area: int) -> dict[str, int]:
    white = ~ink
    n, _, stats = _components(white, 4)
    if n <= 1:
        return {"region_count": 0, "closed_region_count": 0, "tiny_region_count": 0}
    h, w = ink.shape
    s = stats[1:]
    x, y = s[:, cv2.CC_STAT_LEFT], s[:, cv2.CC_STAT_TOP]
    bw, bh = s[:, cv2.CC_STAT_WIDTH], s[:, cv2.CC_STAT_HEIGHT]
    touches = (x == 0) | (y == 0) | (x + bw == w) | (y + bh == h)
    return {
        "region_count": int(len(s)),
        "closed_region_count": int(np.sum(~touches)),
        "tiny_region_count": int(np.sum(s[:, cv2.CC_STAT_AREA] < min_region_area)),
    }


def compute_metrics(
    final_ink: np.ndarray,
    p: LineartParams,
    *,
    source_gray: np.ndarray | None = None,
    raw_ink: np.ndarray | None = None,
    cleaned_ink: np.ndarray | None = None,
) -> dict[str, float | int]:
    """計算指標。source_gray / raw_ink / cleaned_ink 缺少時，以 final_ink 代替對應階段。"""
    raw = raw_ink if raw_ink is not None else final_ink
    cleaned = cleaned_ink if cleaned_ink is not None else final_ink
    m: dict[str, float | int] = {
        "gray_pixel_ratio": gray_pixel_ratio(source_gray, p.gray_low, p.gray_high) if source_gray is not None
        else 0.0,
        "fragment_count": count_small_components(raw, max(p.min_fragment_area, 1)),
        "black_fill_ratio": black_fill_ratio(cleaned, p.line_width_px or 11),
    }
    m.update(region_stats(final_ink, p.min_region_area))
    m["black_pixel_ratio"] = float(final_ink.mean())
    return m


# ---- 主流程 -------------------------------------------------------------------

def process(image: np.ndarray | str | Path, params: LineartParams | dict | None = None, *,
            keep_steps: bool = False) -> LineartResult:
    p = params if isinstance(params, LineartParams) else LineartParams.from_dict(params)
    p.validate()
    img = load_image(image) if isinstance(image, (str, Path)) else image
    source_gray = to_gray(img)

    steps: dict[str, np.ndarray] = {}
    resized = resize_to_target(img, p)
    gray = to_gray(resized)
    raw = binarize(gray, p)
    cleaned = remove_small_fragments(raw, p.min_fragment_area)
    closed = close_gaps(cleaned, p.close_kernel, p.close_iterations)
    final = unify_line_width(closed, p.line_width_px)
    if keep_steps:
        steps = {"resized": resized, "gray": gray, "binary": raw, "cleaned": cleaned, "closed": closed,
                 "final": final}

    metrics = compute_metrics(final, p, source_gray=source_gray, raw_ink=raw, cleaned_ink=cleaned)
    return LineartResult(ink=final, metrics=metrics, params=p, steps=steps)


def process_file(src: str | Path, dst: str | Path, params: LineartParams | dict | None = None) -> LineartResult:
    result = process(src, params)
    save_1bit_png(result.ink, dst, dpi=result.params.dpi)
    return result


def params_dict(p: LineartParams) -> dict[str, Any]:
    return asdict(p)
