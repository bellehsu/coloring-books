"""匯出：把頁面線稿貼到完整版面（1-bit），組成可直接列印的 PDF。"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from app.services.captions import render_caption_page
from app.services.caption_text import normalize_caption
from app.services.imageio import ink_to_bilevel
from app.services.layout import Layout


def layout_print_page(ink: np.ndarray, layout: Layout,
                 captions: tuple[str | None, str | None] = (None, None),
                 font_path: Path | None = None,
                 show: tuple[bool, bool] = (True, True)) -> Image.Image:
    """ink（True＝黑）→ 版面大小的 1-bit 白底圖，內容貼在 content_offset_px。

    captions＝(中文, 英文)，show＝(印中文, 印英文)（page.caption_show_zh／_en）：
    - 至少一行「開著而且有字」：線稿縮到上方、那幾行以空心字印在底部的文字帶
      （captions.render_caption_page；字型不能用 → CaptionFontMissing）；
    - 兩個都關：文字帶照留、整條空白（給小朋友自己寫），線稿一樣縮到上方；不需要字型；
    - 其他（開著的行都沒字）：和以前一模一樣（沒有文字帶）。
    """
    show_zh, show_en = show
    zh, en = (normalize_caption(c) if on else None for c, on in zip(captions, (show_zh, show_en)))
    if zh or en or not (show_zh or show_en):
        return render_caption_page(ink, layout, zh, en, font_path)
    page = Image.new("1", layout.page_px, 1)
    page.paste(ink_to_bilevel(ink), layout.content_offset_px)
    return page


def build_pdf(pages: list[Image.Image], dpi: int = 300) -> bytes:
    buf = io.BytesIO()
    first, *rest = pages
    first.save(buf, "PDF", resolution=dpi, save_all=True, append_images=rest)
    return buf.getvalue()


def png_bytes(page: Image.Image, dpi: int = 300) -> bytes:
    buf = io.BytesIO()
    page.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()
