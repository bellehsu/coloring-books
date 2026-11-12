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
    largest_component,
    page_placement,
)
from app.services.page_compose import composite_matches

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


def _ring():
    """外圈 255、中間 0 的「甜甜圈」：BiRefNet 把白色臉／頭盔內部當成背景挖掉的樣子。"""
    a = np.zeros((60, 60), np.uint8)
    a[10:50, 10:50] = 255
    a[20:40, 20:40] = 0
    return a


def test_largest_component_fills_enclosed_hole():
    from app.services.compose import largest_component
    import io

    a = _ring()
    a[5:10, 10:50] = 90  # 外緣柔邊：保留原灰階
    a[20:40, 20] = 100  # 洞內緣的柔邊（≤128）：在洞裡 → 補成 255
    a[29:31, 29:31] = 255  # 洞中央與主體分離的小碎塊：一起補滿
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(a)))))
    assert (o[10:50, 10:50] == 255).all()
    assert (o[8:10, 10:50] == 90).all()
    assert (o[:5] == 0).all() and (o[:, :8] == 0).all() and (o[52:] == 0).all() and (o[:, 52:] == 0).all()


def test_largest_component_keeps_outside_background():
    from app.services.compose import largest_component
    import io

    # C 字形：開口通到外面 → 裡面仍是背景
    c = np.zeros((60, 60), np.uint8)
    c[10:50, 10:50] = 255
    c[20:40, 20:50] = 0
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(c)))))
    assert (o[20:40, 22:50] == 0).all()
    assert (o[10:20, 10:50] == 255).all() and (o[40:50, 10:50] == 255).all()
    # 角色被圖邊切到：主體與圖邊圍住的區域連到圖邊 → 不是洞
    u = np.zeros((60, 60), np.uint8)
    u[30:60, 10:50] = 255
    u[40:60, 20:40] = 0
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(u)))))
    assert (o[42:60, 22:38] == 0).all()
    # 1 px 外框只靠斜角相連（8 連通仍是一塊）：背景以 4 連通判斷，洞不會從斜角「漏」到外面
    d = np.zeros((40, 40), np.uint8)
    d[10, 10:31] = d[30, 10:31] = 255
    d[10:31, 10] = d[10:31, 30] = 255
    d[10, 10] = 0
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(d)))))
    assert (o[11:30, 11:30] == 255).all()


def test_largest_component_hole_fill_keeps_outer_soft_edge():
    # 補洞只動洞裡：主體外緣的柔邊灰階照舊保留，外面 2 px 以外仍是 0
    from app.services.compose import largest_component
    import io

    a = _ring()
    a[10:50, 50] = 200
    a[10:50, 51] = 60
    o = np.asarray(Image.open(io.BytesIO(largest_component(_png(a)))))
    assert (o[20:40, 20:40] == 255).all()
    assert (o[10:50, 50] == 200).all() and (o[10:50, 51] == 60).all()
    assert (o[:, 53:] == 0).all()


def _mask_png(arr):
    import io

    buf = io.BytesIO()
    Image.fromarray(arr, "L").save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize(("w", "h"), [(300, 10), (10, 300), (400, 40), (40, 400)])
def test_extreme_mask_rejected(w, h):
    """外框短邊 < 16 px 或長寬比 > 8:1 的遮罩：compose 與 largest_component 都丟 ValueError。"""
    pose = Image.new("RGB", (500, 500), (0, 255, 0))
    mask = Image.new("L", (500, 500), 0)
    mask.paste(255, (20, 20, 20 + w, 20 + h))
    with pytest.raises(ValueError, match="太細長"):
        compose(_bg(), pose, mask, DEFAULT_PLACEMENT)
    with pytest.raises(ValueError, match="太細長"):
        largest_component(_mask_png(np.asarray(mask)))


def test_normal_mask_still_accepted():
    pose, mask = _pose()
    compose(_bg(), pose, mask, DEFAULT_PLACEMENT)
    largest_component(_mask_png(np.asarray(mask)))


def test_target_width_clamped(monkeypatch):
    """寬度上限是背景寬的 3 倍（長寬比 8:1 的角色在很寬的背景高度比下也不會超過）。"""
    seen = []
    orig = Image.Image.resize

    def spy(self, size, *a, **k):
        seen.append(size)
        return orig(self, size, *a, **k)

    monkeypatch.setattr(Image.Image, "resize", spy)
    pose = Image.new("RGB", (800, 100), (0, 255, 0))
    mask = Image.new("L", (800, 100), 255)  # 8:1，剛好可接受
    compose(_bg(100, 300), pose, mask, Placement(0.5, 0.5, 1.0, False))
    assert max(w for w, _ in seen) <= 300
