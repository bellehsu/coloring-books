"""版面計算：紙張尺寸 + 列印模式 → 內容像素尺寸與生成尺寸。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import log


class PaperSize(str, Enum):
    A4 = "a4"
    A5 = "a5"
    B5 = "b5"
    LETTER = "letter"
    SQUARE_21 = "square_21"


class PrintMode(str, Enum):
    HOME = "home"
    PRINT_SHOP = "print_shop"


PAPER_MM: dict[PaperSize, tuple[float, float]] = {
    PaperSize.A4: (210, 297),
    PaperSize.A5: (148, 210),
    PaperSize.B5: (182, 257),
    PaperSize.LETTER: (215.9, 279.4),
    PaperSize.SQUARE_21: (210, 210),
}

# 可用的生成尺寸（寬, 高）
GEN_SIZES: tuple[tuple[int, int], ...] = (
    (1024, 1024), (896, 1152), (864, 1216), (832, 1216), (768, 1344),
)

HOME_MARGIN_MM = 10  # 家用白邊
BLEED_MM = 3  # 印刷廠出血
SAFE_MM = 5  # 印刷廠安全區


RATIO_GROUP: dict[PaperSize, str] = {
    PaperSize.A4: "iso",
    PaperSize.A5: "iso",
    PaperSize.B5: "iso",
    PaperSize.LETTER: "letter",
    PaperSize.SQUARE_21: "square",
}


def ratio_group(size: PaperSize) -> str:
    """同一比例群組的紙張可共用生成尺寸與版面參數。"""
    return RATIO_GROUP[size]


@dataclass(frozen=True)
class Layout:
    paper_mm: tuple[float, float]
    content_mm: tuple[float, float]
    content_px: tuple[int, int]
    generation_size: tuple[int, int]
    safe_inset_mm: float | None
    page_px: tuple[int, int]  # PDF 一頁的像素
    content_offset_px: tuple[int, int]  # 內容貼在頁面上的位置
    trim_box_px: tuple[int, int, int, int] | None  # 內容座標系 x0,y0,x1,y1（僅印刷廠）
    safe_box_px: tuple[int, int, int, int] | None  # 內容座標系 x0,y0,x1,y1（僅印刷廠）
    ratio_group: str


def compute_layout(size: PaperSize, mode: PrintMode, dpi: int = 300) -> Layout:
    pw, ph = PAPER_MM[size]
    if mode is PrintMode.HOME:
        delta, safe = -2 * HOME_MARGIN_MM, None
    else:
        delta, safe = 2 * BLEED_MM, BLEED_MM + SAFE_MM
    cw, ch = pw + delta, ph + delta
    px = (round(cw / 25.4 * dpi), round(ch / 25.4 * dpi))
    gen = min(GEN_SIZES, key=lambda wh: abs(log((wh[0] / wh[1]) / (cw / ch))))
    paper_px = (round(pw / 25.4 * dpi), round(ph / 25.4 * dpi))

    if mode is PrintMode.HOME:
        # 家用：頁面為紙張，內容置中並留白
        page_px = paper_px
        offset = ((paper_px[0] - px[0]) // 2, (paper_px[1] - px[1]) // 2)
        trim = safe_box = None
    else:
        # 印刷廠：內容即頁面，裁切框位於出血邊內側，安全區再內縮
        page_px = px
        offset = (0, 0)
        b = round(BLEED_MM / 25.4 * dpi)
        s = round(SAFE_MM / 25.4 * dpi)
        trim = (b, b, b + paper_px[0], b + paper_px[1])
        safe_box = (b + s, b + s, b + paper_px[0] - s, b + paper_px[1] - s)

    return Layout((pw, ph), (cw, ch), px, gen, safe, page_px, offset, trim, safe_box,
                  ratio_group(size))
