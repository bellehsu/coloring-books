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


@dataclass(frozen=True)
class Layout:
    paper_mm: tuple[float, float]
    content_mm: tuple[float, float]
    content_px: tuple[int, int]
    generation_size: tuple[int, int]
    safe_inset_mm: float | None


def compute_layout(size: PaperSize, mode: PrintMode, dpi: int = 300) -> Layout:
    pw, ph = PAPER_MM[size]
    if mode is PrintMode.HOME:
        delta, safe = -2 * HOME_MARGIN_MM, None
    else:
        delta, safe = 2 * BLEED_MM, BLEED_MM + SAFE_MM
    cw, ch = pw + delta, ph + delta
    px = (round(cw / 25.4 * dpi), round(ch / 25.4 * dpi))
    gen = min(GEN_SIZES, key=lambda wh: abs(log((wh[0] / wh[1]) / (cw / ch))))
    return Layout((pw, ph), (cw, ch), px, gen, safe)
