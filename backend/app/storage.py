"""檔案儲存：所有檔案以 storage_dir 為根的相對路徑記錄在資料庫。"""

import io
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MIN_SIDE = 64
# 上傳背景：解碼前先擋像素數（避免小檔案、超大尺寸的圖把記憶體吃光）；48MP 手機照片仍可通過
UPLOAD_MAX_PIXELS = 64_000_000
UPLOAD_TOO_MANY_PIXELS = "圖片像素太多（最多 6400 萬像素）"


class ImageRejected(ValueError):
    """上傳圖片不合格（格式、大小或尺寸）。訊息說明原因。"""


class TooManyPixels(ImageRejected):
    """像素數超過上限（解碼前就判斷）。API 回 422。"""


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def generation_image(self, book_id: int, gen_id: int) -> str:
        return f"books/{book_id}/generations/{gen_id}.png"

    def generation_mask(self, book_id: int, gen_id: int) -> str:
        return f"books/{book_id}/generations/{gen_id}_mask.png"

    def sketch(self, book_id: int, page_id: int) -> str:
        """舊資料的構圖草圖位置（Phase 9 起不再寫入；頁面刪除時連同資料夾一起清掉）。"""
        return f"books/{book_id}/pages/{page_id}/sketch.png"

    def lineart(self, book_id: int, page_id: int, key: str) -> str:
        return f"books/{book_id}/pages/{page_id}/lineart_{key}.png"

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


_EXIF_ORIENTATION = 0x0112


def _high_depth_to_l(im: Image.Image) -> Image.Image:
    """I;16／I／F 灰階縮成 8-bit L。Pillow 直接 convert 會把超過 255 的值截掉，整張變白。"""
    if im.mode.startswith("I;16"):
        arr = np.asarray(im).astype(np.uint16) >> 8
    elif im.mode == "I":
        arr = np.clip(np.asarray(im).astype(np.int64), 0, 65535)
        if arr.max(initial=0) > 255:
            arr = arr >> 8
    else:  # F
        arr = np.nan_to_num(np.asarray(im).astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        arr = np.clip(arr, 0.0, None)
        peak = arr.max(initial=0.0)
        arr = arr * 255.0 if peak <= 1.0 else arr / peak * 255.0
        arr = np.round(arr)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))  # 2-D uint8 → L


def _flatten_on_white(im: Image.Image) -> Image.Image:
    """轉成 RGB；有透明度的圖先合成到白底；16-bit／32-bit 灰階（I;16、I、F）先縮成 8-bit。"""
    if im.mode.startswith("I") or im.mode == "F":
        return _high_depth_to_l(im).convert("RGB")
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (
        im.mode == "P" and "transparency" in im.info
    )
    if has_alpha:
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return im.convert("RGB")


def load_rgb(
    data: bytes,
    *,
    max_bytes: int = 10_000_000,
    max_side: int = 8192,
    max_pixels: int | None = None,
    draft_size: tuple[int, int] | None = None,
) -> tuple[Image.Image, tuple[int, int]]:
    """驗證上傳圖片（PNG/JPEG/WebP）並解碼成 RGB Image。回傳 (圖, 轉正後的原尺寸 (寬, 高))。
    max_pixels：像素數上限，只看檔頭宣告的尺寸、不解碼；超過丟 TooManyPixels。
    draft_size：（轉正後的）需要尺寸；JPEG 會以 1/2、1/4、1/8 縮小解碼，每邊仍不小於它，省時省記憶體。"""
    if len(data) > max_bytes:
        raise ImageRejected(f"檔案過大：{len(data)} bytes，上限 {max_bytes}")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise ImageRejected(f"不支援的格式：{im.format}（僅接受 PNG/JPEG/WebP）")
            w, h = im.size
            if max_pixels is not None and w * h > max_pixels:
                raise TooManyPixels(UPLOAD_TOO_MANY_PIXELS)
            if min(w, h) < MIN_SIDE:
                raise ImageRejected(f"圖片太小：{w}x{h}，最短邊至少 {MIN_SIDE}px")
            if max(w, h) > max_side:
                raise ImageRejected(f"圖片太大：{w}x{h}，最長邊上限 {max_side}px")
            rotated = im.getexif().get(_EXIF_ORIENTATION) in (5, 6, 7, 8)
            if rotated:
                w, h = h, w
            if draft_size is not None and im.format == "JPEG":
                dw, dh = draft_size
                im.draft("RGB", (dh, dw) if rotated else (dw, dh))  # draft 用檔案內的方向
            im = ImageOps.exif_transpose(im)
            rgb = _flatten_on_white(im)
    except Image.DecompressionBombError as e:
        # Pillow 在開檔時就擋下宣告尺寸超過其上限（約 1.8 億像素）的圖
        if max_pixels is not None:
            raise TooManyPixels(UPLOAD_TOO_MANY_PIXELS) from e
        raise ImageRejected(f"無法解析為圖片：{e}") from e
    except (UnidentifiedImageError, OSError) as e:
        raise ImageRejected(f"無法解析為圖片：{e}") from e
    return rgb, (w, h)


def normalize_png(
    data: bytes, *, max_bytes: int = 10_000_000, max_side: int = 8192, max_pixels: int | None = None
) -> tuple[bytes, tuple[int, int]]:
    """驗證上傳圖片（PNG/JPEG/WebP）並轉成 RGB PNG。回傳 (png bytes, (寬, 高))。"""
    rgb, size = load_rgb(data, max_bytes=max_bytes, max_side=max_side, max_pixels=max_pixels)
    buf = io.BytesIO()
    rgb.save(buf, "PNG")
    return buf.getvalue(), size
