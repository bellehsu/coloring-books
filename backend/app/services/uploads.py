"""上傳圖片（背景與主角）的共同驗證與尺寸處理。

背景：等比例縮放到剛好蓋滿再置中裁切（cover_crop）；主角：等比例縮放到放得進再貼在白底（letterbox），絕不拉伸。
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlmodel import Session

from app.models import Generation
from app.services.page_compose import abandon, finish_stored
from app.storage import UPLOAD_MAX_PIXELS, ImageRejected, Storage, TooManyPixels, load_rgb

MAX_UPLOAD_BYTES = 10_000_000  # 背景與主角上傳共用的檔案大小上限


def read_upload(file: UploadFile, size: tuple[int, int]):
    """上傳圖片的共同驗證（背景與主角）：大小上限、格式、像素數；回傳 (RGB 圖, 原尺寸)。
    超過像素上限 422，其他不接受的檔案 400。"""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"檔案過大，上限 {MAX_UPLOAD_BYTES} bytes")
    width, height = size
    try:
        # JPEG 縮小解碼到約兩倍目標尺寸（LANCZOS 縮放仍有餘裕），不先整張轉成 PNG 再解回來
        return load_rgb(
            data, max_bytes=MAX_UPLOAD_BYTES, max_pixels=UPLOAD_MAX_PIXELS,
            draft_size=(2 * width, 2 * height),
        )
    except TooManyPixels as e:
        raise HTTPException(422, str(e)) from e
    except ImageRejected as e:
        raise HTTPException(400, str(e)) from e


def mark_stored(session: Session, storage: Storage, gen: Generation, rel: str, gone: str) -> None:
    """finish_stored 並提交；暫存列已經不在（極少見）就刪物件、回 409。"""
    try:
        res = finish_stored(session, gen.id, rel)
        session.commit()
    except Exception:
        abandon(session, storage, gen.id, rel)
        raise
    if res.rowcount == 0:
        abandon(session, storage, gen.id, rel)
        raise HTTPException(409, gone)
    session.refresh(gen)


def cover_crop(
    img: Image.Image, size: tuple[int, int]
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """LANCZOS 縮放到蓋滿 size 後置中裁切。回傳 (圖, 裁切框)；裁切框是縮放後座標 (l, t, r, b)。"""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    sw, sh = max(tw, round(img.width * scale)), max(th, round(img.height * scale))
    scaled = img.resize((sw, sh), Image.Resampling.LANCZOS)
    left, top = (sw - tw) // 2, (sh - th) // 2
    box = (left, top, left + tw, top + th)
    return scaled.crop(box), box


def letterbox(
    img: Image.Image, size: tuple[int, int], fill=(255, 255, 255)
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """上傳的主角：LANCZOS 等比例縮放到剛好放得進 size（不裁切、不拉伸），置中貼在白底上。
    回傳 (圖, 貼上的範圍 (l, t, r, b))。"""
    tw, th = size
    scale = min(tw / img.width, th / img.height)
    sw, sh = min(tw, max(1, round(img.width * scale))), min(th, max(1, round(img.height * scale)))
    canvas = Image.new("RGB", size, fill)
    left, top = (tw - sw) // 2, (th - sh) // 2
    canvas.paste(img.convert("RGB").resize((sw, sh), Image.Resampling.LANCZOS), (left, top))
    return canvas, (left, top, left + sw, top + sh)
