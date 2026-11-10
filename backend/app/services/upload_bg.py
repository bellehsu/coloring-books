"""上傳背景：等比例縮放到剛好蓋滿目標尺寸，再置中裁切（絕不拉伸）。"""

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
