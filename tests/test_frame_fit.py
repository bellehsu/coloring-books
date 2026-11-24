"""frame_fit：找出背景相框的內部區域，並計算肖像的位置。"""

import io

import cv2
import numpy as np
import pytest
from PIL import Image

from app.services import frame_fit

W, H = 832, 1216
SEED = frame_fit.DEFAULT_SEED


def _canvas() -> np.ndarray:
    return np.full((H, W, 3), 255, np.uint8)


def _img(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, "RGB")


def _oval(arr: np.ndarray) -> None:
    cv2.ellipse(arr, (416, 600), (300, 480), 0, 0, 360, (0, 0, 0), 8)


def test_oval_frame_found():
    arr = _canvas()
    _oval(arr)
    mask = frame_fit.find_interior(_img(arr), SEED)
    assert mask is not None and mask.shape == (H, W) and mask.dtype == bool
    assert mask[int(SEED[1] * H), int(SEED[0] * W)]
    assert 0.40 < mask.mean() < 0.60
    assert not mask[0, 0]


def test_arch_open_at_bottom_counts():
    arr = _canvas()
    cv2.line(arr, (120, 300), (120, H - 1), (0, 0, 0), 8)
    cv2.line(arr, (712, 300), (712, H - 1), (0, 0, 0), 8)
    cv2.ellipse(arr, (416, 300), (296, 220), 0, 180, 360, (0, 0, 0), 8)
    assert frame_fit.find_interior(_img(arr), SEED) is not None


def test_scene_without_frame_is_none():
    arr = _canvas()
    cv2.line(arr, (0, 800), (W - 1, 800), (0, 0, 0), 8)
    cv2.circle(arr, (600, 200), 80, (0, 0, 0), 8)
    assert frame_fit.find_interior(_img(arr), SEED) is None


def test_tiny_region_is_none():
    arr = _canvas()
    cx, cy = int(SEED[0] * W), int(SEED[1] * H)
    cv2.rectangle(arr, (cx - 30, cy - 30), (cx + 30, cy + 30), (0, 0, 0), 8)
    assert frame_fit.find_interior(_img(arr), SEED) is None


def test_seed_on_line_moves_to_white():
    cx, cy = int(SEED[0] * W), int(SEED[1] * H)
    # 圓環的上緣剛好通過種子像素
    arr3 = _canvas()
    cv2.ellipse(arr3, (cx, cy + 300), (300, 300), 0, 0, 360, (0, 0, 0), 8)
    assert arr3[cy, cx].sum() == 0
    mask = frame_fit.find_interior(_img(arr3), SEED)
    assert mask is not None
    assert mask[cy + 300, cx]


def test_holes_filled():
    arr = _canvas()
    _oval(arr)
    arr[560:640, 300:380] = 0
    mask = frame_fit.find_interior(_img(arr), SEED)
    assert mask is not None
    assert mask[560:640, 300:380].all()


def test_fit_formula():
    interior = np.zeros((1216, 832), bool)
    interior[200:900, 100:700] = True
    cx, cy, height = frame_fit.fit_portrait(interior, (400, 800))
    assert height == pytest.approx(700 * 0.92 / 0.62 / 1216, abs=1e-3)
    assert (cy - height / 2) * 1216 == pytest.approx(200 + 0.08 * 700, abs=1.5)
    assert cx == pytest.approx(400 / 832, abs=1e-3)


def test_interior_png_roundtrip():
    mask = np.zeros((20, 30), bool)
    mask[5:10, 5:10] = True
    img = Image.open(io.BytesIO(frame_fit.interior_png(mask)))
    assert img.mode == "L"
    assert set(np.unique(np.array(img))) == {0, 255}
