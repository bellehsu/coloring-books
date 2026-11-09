"""合成服務測試：確定性貼圖、位置驗證、超出邊界裁切。"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app.services.compose import DEFAULT_PLACEMENT, Placement, compose

BG = (200, 220, 255)
FG = (255, 0, 0)


def _bg(w=200, h=300):
    return Image.new("RGB", (w, h), BG)


def _pose():
    """100x100 畫布，角色是左半紅、右半藍的 40x60 方塊（置中）。"""
    img = Image.new("RGB", (100, 100), (0, 255, 0))
    mask = Image.new("L", (100, 100), 0)
    for x in range(30, 70):
        for y in range(20, 80):
            img.putpixel((x, y), FG if x < 50 else (0, 0, 255))
            mask.putpixel((x, y), 255)
    return img, mask


def test_center_is_character_and_corners_are_background():
    pose, mask = _pose()
    out = compose(_bg(), pose, mask, Placement(0.5, 0.5, 0.6, False))
    assert out.size == (200, 300) and out.mode == "RGB"
    assert out.getpixel((90, 150)) == FG
    for xy in [(0, 0), (199, 0), (0, 299), (199, 299)]:
        assert out.getpixel(xy) == BG


def test_flip_swaps_left_right():
    pose, mask = _pose()
    a = compose(_bg(), pose, mask, Placement(0.5, 0.5, 0.6, False))
    b = compose(_bg(), pose, mask, Placement(0.5, 0.5, 0.6, True))
    assert a.getpixel((90, 150)) == FG
    assert b.getpixel((90, 150)) != FG
    assert b.getpixel((110, 150)) == FG


def test_height_ratio():
    pose, mask = _pose()
    out = compose(_bg(), pose, mask, Placement(0.5, 0.5, 0.6, False))
    arr = np.array(out).astype(int)
    diff = np.abs(arr - np.array(BG)).sum(axis=2) > 60
    rows = np.where(diff.any(axis=1))[0]
    assert abs((rows.max() - rows.min() + 1) - 0.6 * 300) <= 2


def test_compose_clips_out_of_bounds():
    pose, mask = _pose()
    out = compose(_bg(), pose, mask, Placement(0.0, 1.0, 1.0, False))
    assert out.size == (200, 300)


def test_empty_mask_raises():
    pose, _ = _pose()
    with pytest.raises(ValueError, match="遮罩是空的"):
        compose(_bg(), pose, Image.new("L", (100, 100), 0), DEFAULT_PLACEMENT)


def test_placement_validate():
    DEFAULT_PLACEMENT.validate()
    with pytest.raises(ValueError):
        Placement(0.5, 0.5, 0.05, False).validate()
    with pytest.raises(ValueError):
        Placement(1.2, 0.5, 0.5, False).validate()


def test_placement_dict_and_key():
    p = Placement.from_dict({"cx": 0.123456, "cy": 0.5, "height": 0.6, "flip": True})
    assert p.flip is True
    assert p.key() == (0.1235, 0.5, 0.6, True)
    assert Placement.from_dict(p.to_dict()) == p
    assert DEFAULT_PLACEMENT == Placement(0.5, 0.66, 0.6, False)
