"""檔案儲存：所有檔案以 storage_dir 為根的相對路徑記錄在資料庫。"""

import io
import os
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MIN_SIDE = 64


class ImageRejected(ValueError):
    """上傳圖片不合格（格式、大小或尺寸）。訊息說明原因。"""


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def generation_image(self, book_id: int, gen_id: int) -> str:
        return f"books/{book_id}/generations/{gen_id}.png"

    def sketch(self, book_id: int, page_id: int) -> str:
        return f"books/{book_id}/pages/{page_id}/sketch.png"

    def sketch_snapshot(self, book_id: int, page_id: int, gen_id: int) -> str:
        return f"books/{book_id}/pages/{page_id}/sketch_{gen_id}.png"

    def lineart(self, book_id: int, page_id: int) -> str:
        return f"books/{book_id}/pages/{page_id}/lineart.png"

    def preview(self, gen_id: int, key: str) -> str:
        return f"cache/previews/{gen_id}_{key}.png"

    def abs(self, rel: str) -> Path:
        """相對路徑轉絕對路徑；解析後若跳出 root（.. 或絕對路徑）則丟 ValueError。"""
        root = self.root.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"路徑超出儲存根目錄：{rel!r}")
        return path

    def write(self, rel: str, data: bytes) -> Path:
        """先寫暫存檔再 rename，避免讀到寫一半的檔案。"""
        path = self.abs(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path

    def delete(self, rel: str) -> None:
        self.abs(rel).unlink(missing_ok=True)


def normalize_png(
    data: bytes, *, max_bytes: int = 10_000_000, max_side: int = 8192
) -> tuple[bytes, tuple[int, int]]:
    """驗證上傳圖片（PNG/JPEG/WebP）並轉成 RGB PNG。回傳 (png bytes, (寬, 高))。"""
    if len(data) > max_bytes:
        raise ImageRejected(f"檔案過大：{len(data)} bytes，上限 {max_bytes}")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise ImageRejected(f"不支援的格式：{im.format}（僅接受 PNG/JPEG/WebP）")
            w, h = im.size
            if min(w, h) < MIN_SIDE:
                raise ImageRejected(f"圖片太小：{w}x{h}，最短邊至少 {MIN_SIDE}px")
            if max(w, h) > max_side:
                raise ImageRejected(f"圖片太大：{w}x{h}，最長邊上限 {max_side}px")
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            has_alpha = im.mode in ("RGBA", "LA", "PA") or (
                im.mode == "P" and "transparency" in im.info
            )
            if has_alpha:
                rgba = im.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                rgb = Image.alpha_composite(bg, rgba).convert("RGB")
            else:
                rgb = im.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImageRejected(f"無法解析為圖片：{e}") from e
    buf = io.BytesIO()
    rgb.save(buf, "PNG")
    return buf.getvalue(), (w, h)
