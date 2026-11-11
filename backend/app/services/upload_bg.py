"""上傳圖片的尺寸處理：背景等比例縮放到剛好蓋滿再置中裁切；主角等比例縮放到放得進再貼在白底（絕不拉伸）。"""

from PIL import Image


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
