"""experiments/lineart_methods：線稿擷取方法（Route C 實驗）與產品的 xdog。全部用合成測試圖。"""

import cv2
import numpy as np
import pytest

from app.services import lineart_extract
from experiments import lineart_methods as le
from app.services.lineart_post import process

H, W = 400, 300
LIGHT_BLUE = (140, 180, 235)
PINK = (240, 170, 180)


def color_illustration() -> np.ndarray:
    """白底；淡藍色圓與粉紅色方塊，各有 4 px 黑色外框；圓內有一道較深的藍色陰影。"""
    img = np.full((H, W, 3), 255, np.uint8)
    cv2.circle(img, (150, 130), 80, LIGHT_BLUE, -1)
    cv2.ellipse(img, (150, 170), (50, 20), 0, 0, 360, (90, 130, 200), -1)  # 陰影（中間調）
    cv2.circle(img, (150, 130), 80, (20, 20, 20), 4)
    cv2.rectangle(img, (60, 260), (240, 360), PINK, -1)
    cv2.rectangle(img, (60, 260), (240, 360), (20, 20, 20), 4)
    return img


def ink(out: np.ndarray) -> np.ndarray:
    return out < 128


def ring_hit_ratio(mask: np.ndarray) -> float:
    """圓形外框上（半徑 80）有被畫成黑線的比例。"""
    ring = np.zeros(mask.shape, np.uint8)
    cv2.circle(ring, (150, 130), 80, 1, 3)
    near = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return float(near[ring > 0].mean())


@pytest.mark.parametrize("method", ["luma", "xdog", "color_edges"])
def test_methods_find_outline_and_output_is_binary_gray(method):
    out = le.extract(color_illustration(), {"method": method})
    assert out.shape == (H, W) and out.dtype == np.uint8
    assert set(np.unique(out)) <= {0, 255}
    assert ring_hit_ratio(ink(out)) > 0.9


@pytest.mark.parametrize("method", ["luma", "xdog"])
def test_flat_fill_is_not_ink(method):
    out = ink(le.extract(color_illustration(), {"method": method}))
    # 粉紅方塊內部（離外框 >10 px）應該是白的
    assert out[275:345, 75:225].mean() < 0.01
    # 淡藍圓內部（陰影以外）
    assert out[80:120, 120:180].mean() < 0.01


def test_luma_ignores_mid_tone_shading_but_threshold_is_tunable():
    img = color_illustration()
    shade = (slice(165, 176), slice(120, 181))
    assert ink(le.extract(img, {"method": "luma"}))[shade].mean() < 0.01
    assert ink(le.extract(img, {"method": "luma", "threshold": 200}))[shade].mean() > 0.9


def test_none_passes_gray_through():
    img = color_illustration()
    out = le.extract(img, {"method": "none"})
    assert np.array_equal(out, cv2.cvtColor(img, cv2.COLOR_RGB2GRAY))
    assert le.extract(img, None).shape == (H, W)  # None = none


def test_luma_result_gives_closed_regions_after_postprocess():
    out = le.extract(color_illustration(), {"method": "luma"})
    res = process(out, dict(target_width=W, target_height=H, line_width_px=3, min_fragment_area=10,
                            close_kernel=3, min_region_area=200))
    assert res.metrics["closed_region_count"] >= 2
    assert res.metrics["gray_pixel_ratio"] == 0.0


def test_spec_defaults_and_roundtrip():
    s = le.ExtractSpec.from_dict({"method": "xdog", "sigma": 1.2})
    assert s.method == "xdog" and s.params["sigma"] == 1.2 and s.params["phi"] == le.METHODS["xdog"]["phi"]
    assert s.to_dict() == {"method": "xdog", **s.params}
    assert le.ExtractSpec.from_dict(None).method == "none"


@pytest.mark.parametrize("d, msg", [
    ({"method": "magic"}, "method"),
    ({"method": "luma", "sigma": 1}, "未知"),
    ({"method": "luma", "threshold": 300}, "threshold"),
    ({"method": "xdog", "sigma": 0}, "sigma"),
    ({"method": "color_edges", "canny_low": 120, "canny_high": 100}, "canny_low"),
    ({"method": "luma", "threshold": "dark"}, "threshold"),
])
def test_spec_validation(d, msg):
    with pytest.raises(ValueError, match=msg):
        le.ExtractSpec.from_dict(d)


def test_accepts_rgba_and_gray_input():
    img = color_illustration()
    rgba = np.dstack([img, np.full((H, W), 255, np.uint8)])
    assert np.array_equal(le.extract(rgba, {"method": "luma"}), le.extract(img, {"method": "luma"}))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    assert le.extract(gray, {"method": "xdog"}).shape == (H, W)


# ---- hybrid：luma 為主，深色區用 xdog，補回 luma 漏掉的淺色長線 ----------------------------

HH, HW = 600, 450


def hybrid_scene() -> np.ndarray:
    """左上：深藍「夜空窗」含白色星點；右側：淺藍色長線（luma 會漏）；下方：黑框衣服內有淺藍條紋。"""
    img = np.full((HH, HW, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (200, 200), (30, 50, 110), -1)  # 夜空
    for x, y in ((60, 60), (140, 90), (90, 160)):
        cv2.circle(img, (x, y), 6, (255, 255, 255), -1)  # 星星
    cv2.rectangle(img, (20, 20), (200, 200), (10, 10, 10), 4)
    cv2.line(img, (330, 40), (330, 330), (120, 170, 230), 3)  # 淺藍長線（櫥櫃邊）
    cv2.rectangle(img, (120, 380), (330, 560), (10, 10, 10), 4)  # 衣服外框
    for y in range(400, 550, 16):
        cv2.line(img, (128, y), (322, y), (120, 170, 230), 3)  # 衣服條紋（與淺藍長線同粗細）
    return img


def test_hybrid_uses_xdog_in_dark_regions():
    img = hybrid_scene()
    sky = (slice(40, 180), slice(40, 180))
    luma = ink(le.extract(img, {"method": "luma"}))
    hyb = ink(le.extract(img, {"method": "hybrid"}))
    assert luma[sky].mean() > 0.9  # luma 把整片夜空當成線
    assert hyb[sky].mean() < 0.15  # hybrid 只留星星等輪廓
    assert hyb[sky].any()


def test_hybrid_recovers_long_light_lines_but_not_stripes():
    img = hybrid_scene()
    luma = ink(le.extract(img, {"method": "luma"}))
    xd = ink(le.extract(img, {"method": "xdog"}))
    # 測試圖很小，衣服區域佔全圖 14%；把「大片空白」門檻設成 20%（實際 832×1216 的圖用預設 5%）
    hyb = ink(le.extract(img, {"method": "hybrid", "open_space_frac": 0.2}))
    light_line = (slice(60, 310), slice(322, 339))
    assert luma[light_line].mean() < 0.01
    assert hyb[light_line].any(axis=1).mean() > 0.9  # 每一列都有線
    stripes = (slice(395, 545), slice(140, 310))
    assert xd[stripes].mean() > 0.05  # xdog 會畫出條紋
    assert hyb[stripes].mean() < 0.01  # hybrid 不在已封閉的區域內補線
    frame = (slice(376, 385), slice(150, 300))
    assert hyb[frame].mean() > 0.5  # 黑色外框仍在


def test_hybrid_spec_validation():
    s = le.ExtractSpec.from_dict({"method": "hybrid", "dark_threshold": 100})
    assert s.params["threshold"] == 90 and s.params["sigma"] == le.METHODS["xdog"]["sigma"]
    for bad, msg in [({"open_space_frac": 1.5}, "open_space_frac"), ({"dark_sigma": 0}, "dark_sigma"),
                     ({"min_line_length": -1}, "min_line_length")]:
        with pytest.raises(ValueError, match=msg):
            le.ExtractSpec.from_dict({"method": "hybrid", **bad})


def test_product_extract_xdog_matches_the_experiment_xdog_method():
    """產品的 extract_xdog(sigma) 與實驗表裡 xdog 方法（預設 k／p／eps／phi）逐像素相同，輸入灰階／RGB／RGBA 都接受。"""
    img = color_illustration()
    for sigma in (0.8, 1.2, 1.5):
        want = le.extract(img, {"method": "xdog", "sigma": sigma})
        assert np.array_equal(lineart_extract.extract_xdog(img, sigma), want)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    assert lineart_extract.extract_xdog(gray, 1.2).shape == gray.shape
    rgba = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
    assert np.array_equal(lineart_extract.extract_xdog(rgba, 1.2), lineart_extract.extract_xdog(img, 1.2))


# ---- stroke_fill（單線輪廓）---------------------------------------------------------------


def _band_rgb():
    rgb = np.full((600, 832, 3), 255, np.uint8)
    rgb[292:308] = 0  # 16 px 厚的橫帶
    return rgb


CONTENT = (2244, 3272)  # 實際的 A4 家用內容區；縮到 832 寬時兩條邊緣會被線寬統一合併成一條


def _runs(ink_img: np.ndarray) -> int:
    col_ = ink_img[:, ink_img.shape[1] // 2].astype(int)
    return int((np.diff(col_) == 1).sum() + col_[0])


def test_stroke_fill_params_scale_with_width():
    assert lineart_extract.stroke_fill_params(832) == (10, 13.0)
    assert lineart_extract.stroke_fill_params(1664) == (20, 26.0)
    assert lineart_extract.stroke_fill_params(416) == (5, 6.5)


def test_stroke_fill_thick_band_gives_one_line():
    from app.services import lineart_post

    rgb = _band_rgb()
    gray = lineart_extract.extract_xdog(rgb, 1.2)
    assert _runs(lineart_post.render_ink(gray, CONTENT, 11)) == 2
    assert _runs(lineart_post.render_ink(lineart_extract.fill_outlines(rgb, gray), CONTENT, 11)) == 1


def _disk(radius):
    rgb = np.full((300, 832, 3), 255, np.uint8)
    cv2.circle(rgb, (416, 150), radius, (0, 0, 0), -1)
    mask = np.zeros((300, 832), np.uint8)
    cv2.circle(mask, (416, 150), radius - 4, 1, -1)  # 內部（避開邊緣的抗鋸齒）
    return rgb, mask.astype(bool)


def test_stroke_fill_keeps_solid_disk_outline():
    rgb, inner = _disk(40)
    gray = lineart_extract.extract_xdog(rgb, 1.2)
    out = lineart_extract.fill_outlines(rgb, gray)
    assert np.array_equal(out[inner], gray[inner])


def test_stroke_fill_fills_junction_blob():
    rgb, inner = _disk(12)
    gray = lineart_extract.extract_xdog(rgb, 1.2)
    assert (lineart_extract.fill_outlines(rgb, gray)[inner] == 0).all()


def test_stroke_fill_matches_experiment():
    from pathlib import Path

    from PIL import Image

    from experiments import single_outline

    rgb = np.asarray(Image.open(Path(__file__).parent.parent / "experiments/samples/character_ref.png").convert("RGB"))
    gray = lineart_extract.extract_xdog(rgb, 1.2)
    assert np.array_equal(lineart_extract.stroke_fill(rgb, gray, 70, 10, 13),
                          single_outline.stroke_fill(rgb, gray, 70, 10, 13))


def test_stroke_fill_constants_snapshot():
    msg = "stroke_fill 參數改了：請把 OUTLINE_FILL_VERSION 加 1，再更新這個快照"
    le_ = lineart_extract
    assert (le_.STROKE_FILL_LUMA, le_.STROKE_FILL_RADIUS, le_.STROKE_FILL_KEEP, le_.STROKE_FILL_REF_WIDTH,
            le_.OUTLINE_FILL_VERSION) == (70, 10, 13.0, 832, 1), msg
