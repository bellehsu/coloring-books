"""頁面文字（圖下方的中文字、英文字）的正規化：前端 `normalizeCaption`（lib/limits.ts）要維持相同規則。"""

import unicodedata


def normalize_caption(text: str | None) -> str | None:
    """每一段空白（含換行、U+2028／U+2029、U+0085、全形空格等）變成一個空格；
    移除控制字元（Cc）與格式字元（Cf，例如零寬空格、連接字元、BOM）；去頭尾空白。空的回傳 None。"""
    if text is None:
        return None
    kept = "".join(
        " " if c.isspace() else c
        for c in text
        if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf")
    )
    return " ".join(kept.split()) or None
