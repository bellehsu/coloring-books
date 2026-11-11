"""頁面文字：把中英文字以空心字印在線稿下方的文字帶（Phase 11 spec §4.2、§4.6）。

只在頁面至少有一行文字時使用；沒有文字的頁面由 export.compose_page 照舊處理（一個像素都不變）。

版面（內容座標）：
- 文字帶＝內容高度的 15%，貼齊內容區底部；印刷廠模式則貼齊安全區底部、左右也在安全區內。
- 線稿等比例縮到文字帶上方的區域、水平置中（印刷廠模式的出血邊因此變成白色，著色頁是白紙，可接受）。
- 兩行：中文中心在帶高 30%、字級約帶高 50%；英文中心在 78%、字級約 30%。
  只有一行時，置於帶的垂直中心，用中文的字級。任何一行寬過內容寬度 90% 時縮小字級直到放得下。
- 字型 Noto Sans TC（可變字型）的 "Black"；白色字身、黑色外框（空心字），之後和線稿一樣轉成 1-bit。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import skeletonize

from app.services.layout import Layout

CAPTION_FONT_MISSING = "找不到字型檔，無法印出頁面文字"

BAND_RATIO = 0.15  # 文字帶占內容高度的比例（所有紙張相同）
ZH_CENTER, ZH_SIZE = 0.30, 0.50  # 中文行：中心位置與字級（帶高的比例）
EN_CENTER, EN_SIZE = 0.78, 0.30  # 英文行
SINGLE_CENTER = 0.50  # 只有一行時（用中文字級）
MAX_LINE_RATIO = 0.90  # 一行最寬占內容寬度的比例
FONT_VARIATION = "Black"

# 空心字外框（px，300 DPI）。spec 允許 7–11：
# - Black 字重在帶高 50% 的字級下，筆畫密的中文字（警、機、車的「田」）字腔本來就很小，7–11 都會填黑；
#   能留白的大字腔（「中」的「口」）在 A5 家用（最小的帶，中文約 168 px）用 11 時只剩約 10×10 px，用 9 約 14×14 px。
# - 英文約 101–147 px，外框 11 時相鄰字母的外框幾乎黏在一起、e／a 的字腔全被填滿；9 還分得開。
# - 7 比縮放後的線稿（10–12 px）明顯細，印出來外框看起來比圖淡。
# 取 9：和線稿粗細接近，同時讓大字腔保持白色可塗（實際比較圖見 Phase 11 Task 2 報告）。
CAPTION_STROKE_PX = 9

MIN_ART_STROKE_PX = 10  # 線稿縮小後若線寬量起來小於這個值，膨脹 1 px


class CaptionFontMissing(Exception):
    """有頁面文字但字型檔找不到或讀不了；API 對應 409。"""

    def __init__(self, detail: str | None = None):
        self.detail = detail
        super().__init__(CAPTION_FONT_MISSING)


@lru_cache(maxsize=64)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """依（路徑, 字級）快取，每個 process 只讀一次。失敗不會被快取（lru_cache 不存例外）。"""
    font = ImageFont.truetype(path, size)
    font.set_variation_by_name(FONT_VARIATION)
    return font


def _font(path: Path | None, size: int) -> ImageFont.FreeTypeFont:
    if path is None:
        raise CaptionFontMissing("paths.caption_font 未設定")
    try:
        return load_font(str(path), max(1, size))
    except Exception as e:  # noqa: BLE001 - 找不到、不是字型、沒有 Black 變體都一樣處理
        raise CaptionFontMissing(f"{path}: {type(e).__name__}: {e}") from e


def check_caption_font(path: Path | None) -> str | None:
    """preflight／啟動用：字型可用回傳 None，否則回傳問題描述（含路徑）。"""
    if path is None:
        return "paths.caption_font 未設定"
    if not Path(path).is_file():
        return f"找不到字型檔：{path}"
    try:
        _font(Path(path), 48)
    except CaptionFontMissing as e:
        return f"字型檔無法使用：{e.detail}"
    return None


def band_box(layout: Layout) -> tuple[int, int, int, int]:
    """文字帶（內容座標 x0, y0, x1, y1）。印刷廠模式在安全區內、貼齊安全區底部。"""
    w, h = layout.content_px
    bh = round(h * BAND_RATIO)
    if layout.safe_box_px is not None:
        sx0, _sy0, sx1, sy1 = layout.safe_box_px
        return (sx0, sy1 - bh, sx1, sy1)
    return (0, h - bh, w, h)


def stroke_width_px(ink: np.ndarray) -> float:
    """線寬的保守估計：骨架上（距離轉換值 ×2 − 1）的中位數。奇數線寬剛好，偶數少算 1。"""
    sk = skeletonize(ink)
    if not sk.any():
        return 0.0
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 5)
    return float(np.median(dist[sk]) * 2 - 1)


def scale_art(ink: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """1-bit 線稿縮放：以灰階 LANCZOS 重新取樣、128 重新二值化；線寬量起來 < 10 px 時膨脹 1 px。"""
    gray = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), mode="L")
    out = np.asarray(gray.resize(size, Image.Resampling.LANCZOS)) < 128
    if out.any() and stroke_width_px(out) < MIN_ART_STROKE_PX:
        out = cv2.dilate(out.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    return out


def _lines(zh: str | None, en: str | None) -> list[tuple[str, float, float]]:
    """(文字, 中心位置, 字級)，都是帶高的比例。"""
    if zh and en:
        return [(zh, ZH_CENTER, ZH_SIZE), (en, EN_CENTER, EN_SIZE)]
    return [(zh or en or "", SINGLE_CENTER, ZH_SIZE)]


def _render_line(text: str, size: int, max_w: float, font_path: Path) -> Image.Image:
    """一行空心字（L：白底、白字身、黑外框），裁到實際墨跡範圍。寬過 max_w 時字級往下縮到放得下。"""
    while True:
        font = _font(font_path, size)
        l, t, r, b = font.getbbox(text, stroke_width=CAPTION_STROKE_PX)
        pad = CAPTION_STROKE_PX + 4
        tile = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad), 255)
        ImageDraw.Draw(tile).text((pad - l, pad - t), text, font=font, fill=255,
                                  stroke_width=CAPTION_STROKE_PX, stroke_fill=0)
        ink = np.asarray(tile) < 128
        if not ink.any():
            return Image.new("L", (1, 1), 255)
        ys, xs = np.nonzero(ink)
        tile = tile.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        if tile.width <= max_w or size <= 8:
            return tile
        size = min(size - 1, int(size * max_w / tile.width))


def render_caption_page(ink: np.ndarray, layout: Layout, zh: str | None, en: str | None,
                        font_path: Path | None) -> Image.Image:
    """線稿（True＝黑）＋頁面文字 → 版面大小的 1-bit 白底圖（和 compose_page 一樣貼在 content_offset_px）。"""
    zh = (zh or "").strip() or None
    en = (en or "").strip() or None
    w, h = layout.content_px
    bx0, by0, bx1, by1 = band_box(layout)
    bh = by1 - by0
    max_w = min(MAX_LINE_RATIO * w, bx1 - bx0)
    # 先排好文字（字型有問題時，在做任何影像處理之前就失敗）
    tiles = [(_render_line(text, round(bh * ratio), max_w, font_path), by0 + center * bh)
             for text, center, ratio in _lines(zh, en)]

    # 線稿：等比例縮到帶上方的區域，水平置中
    ih, iw = ink.shape
    s = min(w / iw, by0 / ih)
    aw, ah = max(1, round(iw * s)), max(1, round(ih * s))
    art = scale_art(ink, (aw, ah))
    canvas = np.full((h, w), 255, np.uint8)
    ax, ay = (w - aw) // 2, (by0 - ah) // 2
    canvas[ay:ay + ah, ax:ax + aw][art] = 0
    content = Image.fromarray(canvas, mode="L")

    # 文字：以實際墨跡（含外框線）的中心對齊目標位置
    cx = (bx0 + bx1) / 2
    for tile, cy in tiles:
        content.paste(tile, (round(cx - tile.width / 2), round(cy - tile.height / 2)))

    page = Image.new("1", layout.page_px, 1)
    page.paste(content.convert("1", dither=Image.Dither.NONE), layout.content_offset_px)
    return page
