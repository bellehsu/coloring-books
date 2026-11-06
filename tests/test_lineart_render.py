import io

import cv2
import numpy as np
from PIL import Image

from app.services import lineart_render as lr
from app.services.lineart_render import LineDetail, Stroke, apply_strokes, ink_to_png, render_base


def test_detail_controls_sigma(monkeypatch):
    seen = []

    def fake_extract(img, spec):
        seen.append(spec)
        return np.full(img.shape[:2], 255, np.uint8)

    monkeypatch.setattr(lr, "extract", fake_extract)
    img = np.full((64, 48, 3), 255, np.uint8)
    for d, s in [(LineDetail.SIMPLE, 1.5), (LineDetail.STANDARD, 1.2), (LineDetail.DETAILED, 0.8)]:
        render_base(img, (96, 128), d, 11)
        assert seen[-1] == {"method": "xdog", "sigma": s}


def test_render_base_size_and_width():
    img = np.full((1216, 832, 3), 255, np.uint8)
    cv2.rectangle(img, (200, 300), (600, 900), (0, 0, 0), 6)
    ink, metrics = render_base(img, (2244, 3272), LineDetail.STANDARD, 11)
    assert ink.shape == (3272, 2244) and ink.dtype == bool and metrics["closed_region_count"] >= 1
    col = ink[:, 1122]
    ys = np.flatnonzero(col)
    assert len(ys)
    # first run of black pixels = top edge of rectangle
    end = ys[0]
    while end + 1 < len(col) and col[end + 1]:
        end += 1
    assert 9 <= end - ys[0] + 1 <= 13


def test_apply_strokes_draw_then_erase():
    ink = np.zeros((100, 200), bool)
    strokes = [Stroke("draw", 10, ((0.1, 0.5), (0.9, 0.5))), Stroke("erase", 20, ((0.5, 0.5),))]
    out = apply_strokes(ink, strokes)
    assert out[50, 40] and not out[50, 100] and not ink.any()
    big = apply_strokes(np.zeros((200, 400), bool), strokes)
    assert big[100, 80] and not big[100, 200]


def test_ink_to_png_is_1bit_300dpi():
    im = Image.open(io.BytesIO(ink_to_png(np.eye(64, dtype=bool))))
    assert im.mode == "1" and round(im.info["dpi"][0]) == 300
