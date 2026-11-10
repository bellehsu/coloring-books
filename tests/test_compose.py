"""合成服務測試：確定性貼圖、位置驗證、超出邊界裁切。"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from app.models import Generation, GenKind
from app.services.compose import (
    COMPOSE_VERSION,
    DEFAULT_PLACEMENT,
    Placement,
    compose,
    composite_matches,
    page_placement,
)

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


@pytest.mark.parametrize(
    "d",
    [
        {"cx": None, "cy": 0.5, "height": 0.5},
        {"cx": float("nan"), "cy": 0.5, "height": 0.5},
        {"cx": 0.5, "cy": float("inf"), "height": 0.5},
        {"cx": True, "cy": 0.5, "height": 0.5},
        {"cx": "0.5", "cy": 0.5, "height": 0.5},
        {"cx": 0.5, "cy": 0.5},
        {"cx": 0.5, "cy": 0.5, "height": 0.5, "flip": "false"},
        {"cx": 0.5, "cy": 0.5, "height": 0.5, "flip": 1},
        {"cx": 0.5, "cy": 0.5, "height": 0.05},
        {"cx": 1.2, "cy": 0.5, "height": 0.5},
        None,
    ],
)
def test_from_dict_rejects_bad_input(d):
    with pytest.raises(ValueError):
        Placement.from_dict(d)


def test_from_dict_flip_optional_and_ints_ok():
    p = Placement.from_dict({"cx": 1, "cy": 0, "height": 1})
    assert p == Placement(1.0, 0.0, 1.0, False)


def test_mask_size_mismatch_raises():
    pose, _ = _pose()
    with pytest.raises(ValueError, match="遮罩與角色圖尺寸不同"):
        compose(_bg(), pose, Image.new("L", (50, 50), 255), DEFAULT_PLACEMENT)


def test_rgba_background_flattens_onto_white():
    pose, mask = _pose()
    bg = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    out = compose(bg, pose, mask, Placement(0.5, 0.5, 0.6, False))
    assert out.getpixel((0, 0)) == (255, 255, 255)


def test_pose_alpha_ignored_only_mask_used():
    pose, mask = _pose()
    rgba = pose.convert("RGBA")
    rgba.putalpha(Image.new("L", pose.size, 0))
    a = compose(_bg(), pose, mask, Placement(0.5, 0.5, 0.6, False))
    b = compose(_bg(), rgba, mask, Placement(0.5, 0.5, 0.6, False))
    assert a.getpixel((90, 150)) == b.getpixel((90, 150)) == FG


@pytest.mark.parametrize("w", [200, 201])
def test_left_edge_column(w):
    pose, mask = _pose()  # 角色 40x60，縮放後 height=150 => 寬 100
    out = compose(_bg(w, 300), pose, mask, Placement(0.5, 0.5, 0.5, False))
    cols = [x for x in range(w) if out.getpixel((x, 150)) == FG]
    expected = round(0.5 * w - 100 / 2)
    assert abs(min(cols) - expected) <= 2


def _composite(params: str, kind=GenKind.COMPOSITE, version=COMPOSE_VERSION) -> Generation:
    return Generation(book_id=1, page_id=1, kind=kind, user_text="", prompt="", negative_prompt="",
                      seed=0, width=8, height=8, request_json="", params_json=params,
                      workflow_version=version)


def test_page_placement_defaults_and_parses():
    assert page_placement(None) == DEFAULT_PLACEMENT
    assert page_placement("") == DEFAULT_PLACEMENT
    assert page_placement('{"cx": 0.3, "cy": 0.4, "height": 0.5, "flip": true}') == Placement(0.3, 0.4, 0.5, True)


def test_composite_matches():
    params = json.dumps({"background_generation_id": 1, "pose_generation_id": 2,
                         "placement": DEFAULT_PLACEMENT.to_dict()})
    near = Placement(DEFAULT_PLACEMENT.cx + 0.00001, DEFAULT_PLACEMENT.cy, DEFAULT_PLACEMENT.height, False)
    assert composite_matches(_composite(params), 1, 2, DEFAULT_PLACEMENT)
    assert composite_matches(_composite(params), 1, 2, near)  # 小數第 5 位視為相同
    assert not composite_matches(_composite(params), 3, 2, DEFAULT_PLACEMENT)
    assert not composite_matches(_composite(params), 1, 3, DEFAULT_PLACEMENT)
    assert not composite_matches(_composite(params), 1, 2, Placement(0.5, 0.66, 0.6, True))
    assert not composite_matches(_composite(params, version="0"), 1, 2, DEFAULT_PLACEMENT)
    assert not composite_matches(_composite(params, kind=GenKind.PAGE), 1, 2, DEFAULT_PLACEMENT)
    for bad in ("", "not json", "{}", '{"background_generation_id": 1}'):
        assert not composite_matches(_composite(bad), 1, 2, DEFAULT_PLACEMENT)


def _png(arr):
    import io

    buf = io.BytesIO()
    Image.fromarray(arr, "L").save(buf, "PNG")
    return buf.getvalue()


def _two_blobs():
    a = np.zeros((100, 100), np.uint8)
    a[10:50, 10:50] = 255
    a[80:85, 60:90] = 255
    return a


def test_largest_component_drops_detached_blob():
    from app.services.compose import largest_component
    import io

    out = Image.open(io.BytesIO(largest_component(_png(_two_blobs()))))
    assert out.mode == "L" and out.size == (100, 100)
    o = np.asarray(out)
    assert (o[10:50, 10:50] == 255).all()
    assert int((o > 0).sum()) == 40 * 40
    assert (o[80:85, 60:90] == 0).all()


def test_largest_component_keeps_all_black():
    from app.services.compose import largest_component
    import io

    out = Image.open(io.BytesIO(largest_component(_png(np.zeros((20, 30), np.uint8)))))
    assert out.size == (30, 20) and int(np.asarray(out).max()) == 0


def test_largest_component_soft_edges():
    from app.services.compose import largest_component
    import io

    a = np.zeros((50, 50), np.uint8)
    a[10:30, 10:30] = 255
    a[10:30, 30] = 200
    a[10:30, 31] = 90
    a[10:30, 32] = 40
    a[10:30, 33] = 60  # 超出 2 px 膨脹範圍 → 0
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(a)))))
    assert (o[10:30, 10:30] == 255).all()
    assert (o[10:30, 30] == 200).all()
    assert (o[10:30, 31] == 90).all()
    assert (o[10:30, 32] == 40).all()
    assert (o[10:30, 33] == 0).all()


def test_largest_component_keeps_soft_fringe_but_zeroes_detached_grey():
    from app.services.compose import largest_component
    import io

    a = _two_blobs()
    a[5:10, 10:50] = 90  # 主體上緣的柔邊
    a[82, 60:90] = 255
    a[70:73, 70:73] = 100  # 遠處的灰色碎塊
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(a)))))
    assert (o[8:10, 10:50] == 90).all()
    assert (o[70:73, 70:73] == 0).all() and (o[80:85, 60:90] == 0).all()
