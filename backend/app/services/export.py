"""匯出：把頁面線稿貼到完整版面（1-bit），組成可直接列印的 PDF。"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from app.services.captions import render_caption_page
from app.services.layout import Layout


def compose_page(ink: np.ndarray, layout: Layout,
                 captions: tuple[str | None, str | None] = (None, None),
                 font_path: Path | None = None) -> Image.Image:
    """ink（True＝黑）→ 版面大小的 1-bit 白底圖，內容貼在 content_offset_px。

    captions＝(中文, 英文)：至少一行有字時，線稿縮到上方、文字以空心字印在底部的文字帶
    （captions.render_caption_page；字型不能用 → CaptionFontMissing）。都沒有字時和以前一模一樣。
    """
    zh, en = ((c or "").strip() or None for c in captions)
    if zh or en:
        return render_caption_page(ink, layout, zh, en, font_path)
    content = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), mode="L").convert(
        "1", dither=Image.Dither.NONE
    )
    page = Image.new("1", layout.page_px, 1)
    page.paste(content, layout.content_offset_px)
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
