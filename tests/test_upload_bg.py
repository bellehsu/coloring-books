"""上傳背景的置中裁切（cover crop）。"""

from PIL import Image

from app.services.upload_bg import cover_crop

SIZE = (896, 1152)


def test_cover_crop_panorama():
    out, (left, top, right, bottom) = cover_crop(Image.new("RGB", (4000, 800)), SIZE)
    assert out.size == SIZE
    scaled_w = round(4000 * 1152 / 800)
    assert abs(left - (scaled_w - 896) / 2) <= 1
    assert (right - left, bottom - top) == SIZE


def test_cover_crop_small_upscales():
    out, _ = cover_crop(Image.new("RGB", (64, 64), "red"), SIZE)
    assert out.size == SIZE


def test_cover_crop_never_stretches():
    cell = 20
    src = Image.new("RGB", (400, 200))
    for x in range(400):
        for y in range(200):
            src.putpixel((x, y), (255, 255, 255) if (x // cell + y // cell) % 2 else (0, 0, 0))
    out, _ = cover_crop(src, SIZE)
    scale = 1152 / 200

    def inner_run(points):
        """二值化後第二段（完整的一格）的長度。"""
        bits = [p[0] > 127 for p in points]
        runs, n = [], 1
        for prev, cur in zip(bits, bits[1:]):
            if cur == prev:
                n += 1
            else:
                runs.append(n)
                n = 1
        return runs[1]

    cw = inner_run([out.getpixel((x, 5)) for x in range(out.width)])
    ch = inner_run([out.getpixel((5, y)) for y in range(out.height)])
    assert abs(cw / ch - 1) <= 0.02
    assert abs(cw - cell * scale) <= 3
