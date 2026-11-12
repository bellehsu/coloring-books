"""lineart_post：全部用合成測試圖。"""

import cv2
import numpy as np
import pytest
from PIL import Image

from app.services import lineart_post as lp
from app.services.lineart_post import LineartParams

W, H = 600, 800
SMALL = dict(target_width=W, target_height=H, line_width_px=7, min_fragment_area=30, close_kernel=7,
             min_region_area=500)


def canvas(h=H, w=W) -> np.ndarray:
    return np.full((h, w), 255, np.uint8)


def circles_with_gap(gap_px: int) -> np.ndarray:
    img = canvas()
    cv2.circle(img, (300, 400), 200, 0, 5)
    cv2.circle(img, (300, 400), 80, 0, 5)
    # 在外圈最右側（x≈500）切一個 gap_px 高的缺口
    if gap_px:
        img[400 - gap_px // 2:400 + gap_px - gap_px // 2, 490:515] = 255
    return img


def measured_line_width(ink: np.ndarray) -> float:
    """以距離轉換估計線寬：骨架上的距離值 × 2 的中位數。"""
    from skimage.morphology import skeletonize
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 5)
    sk = skeletonize(ink)
    return float(np.median(dist[sk]) * 2)


def test_params_validation():
    with pytest.raises(ValueError, match="未知"):
        LineartParams.from_dict({"bogus": 1})
    with pytest.raises(ValueError):
        LineartParams.from_dict({"fit": "zoom"})
    with pytest.raises(ValueError):
        LineartParams.from_dict({"adaptive_block_size": 4})
    with pytest.raises(ValueError):
        LineartParams.from_dict({"close_kernel": -1})


@pytest.mark.parametrize("fit", ["contain", "cover", "stretch"])
def test_resize_to_target_shape(fit):
    img = np.zeros((100, 300, 3), np.uint8)
    out = lp.resize_to_target(img, LineartParams(target_width=200, target_height=400, fit=fit))
    assert out.shape == (400, 200, 3)


def test_resize_contain_pads_white():
    img = np.zeros((100, 100), np.uint8)
    out = lp.resize_to_target(img, LineartParams(target_width=200, target_height=400, fit="contain"))
    assert out[0, 0] == 255 and out[200, 100] == 0  # 上方白邊、中央為原圖


def test_rgba_composited_on_white(tmp_path):
    arr = np.zeros((10, 10, 4), np.uint8)  # 全透明黑
    arr[2:4, 2:4] = (0, 0, 0, 255)
    p = tmp_path / "a.png"
    Image.fromarray(arr, "RGBA").save(p)
    rgb = lp.load_image(p)
    assert rgb[0, 0].tolist() == [255, 255, 255]
    assert rgb[3, 3].tolist() == [0, 0, 0]


@pytest.mark.parametrize("method", ["otsu", "fixed", "adaptive"])
def test_binarize_methods(method):
    img = canvas()
    cv2.line(img, (50, 50), (550, 50), 30, 9)
    ink = lp.binarize(img, LineartParams(binarize=method))
    assert ink[50, 300] and not ink[400, 300]


def test_binarize_uniform_image():
    assert not lp.binarize(canvas(), LineartParams()).any()


def test_remove_small_fragments():
    ink = np.zeros((200, 200), bool)
    ink[10:190, 100:106] = True  # 長線
    ink[20:22, 20:22] = True  # 雜點 4 px
    ink[50:55, 50:55] = True  # 雜點 25 px
    out = lp.remove_small_fragments(ink, 30)
    assert out[100, 102] and not out[21, 21] and not out[52, 52]
    assert lp.count_small_components(ink, 30) == 2
    assert np.array_equal(lp.remove_small_fragments(ink, 0), ink)


def test_closing_seals_small_gap_and_increases_closed_regions():
    p = LineartParams(**SMALL)
    raw = lp.binarize(circles_with_gap(4), p)
    before = lp.region_stats(raw, p.min_region_area)["closed_region_count"]
    closed = lp.close_gaps(raw, 9)
    after = lp.region_stats(closed, p.min_region_area)["closed_region_count"]
    assert after == before + 1


def test_unify_line_width_thin_and_thick_lines():
    img = canvas()
    cv2.line(img, (50, 200), (550, 200), 0, 3)
    cv2.line(img, (50, 500), (550, 500), 0, 25)
    ink = lp.binarize(img, LineartParams())
    out = lp.unify_line_width(ink, 11)
    thin = out[150:250]
    thick = out[450:550]
    assert measured_line_width(thin) == pytest.approx(11, abs=2)
    assert measured_line_width(thick) == pytest.approx(11, abs=2)
    assert np.array_equal(lp.unify_line_width(ink, 0), ink)


def test_gray_pixel_ratio():
    img = canvas(100, 100)
    img[:25] = 128  # 25% 灰
    img[25:50] = 0  # 黑不算
    assert lp.gray_pixel_ratio(img, 32, 223) == pytest.approx(0.25)


def test_black_fill_ratio_detects_solid_patches():
    outline = np.zeros((300, 300), bool)
    cv2.rectangle(outline.view(np.uint8), (50, 50), (250, 250), 1, 8)
    solid = outline.copy()
    solid[100:200, 100:200] = True
    assert lp.black_fill_ratio(outline, 11) == 0
    assert lp.black_fill_ratio(solid, 11) == pytest.approx(100 * 100 / 300 / 300, rel=0.1)


def test_region_stats_closed_vs_border_and_tiny():
    ink = np.zeros((300, 300), bool)
    ink[:, 150] = True  # 分成左右兩區（都碰邊界）
    cv2.rectangle(ink.view(np.uint8), (20, 20), (120, 120), 1, 3)  # 封閉大區
    cv2.rectangle(ink.view(np.uint8), (200, 200), (212, 212), 1, 2)  # 封閉小區
    s = lp.region_stats(ink, min_region_area=500)
    assert s["closed_region_count"] == 2
    assert s["tiny_region_count"] == 1
    assert s["region_count"] == 4


def test_process_end_to_end_metrics():
    img = circles_with_gap(4)
    # 加入灰色陰影與雜點
    img[100:140, 100:500] = 150
    rng = np.random.default_rng(0)
    for y, x in rng.integers(20, 780, size=(40, 2)):
        img[y:y + 2, x % 580:x % 580 + 2] = 0
    res = lp.process(np.stack([img] * 3, -1), dict(SMALL), keep_steps=True)
    m = res.metrics
    assert res.ink.shape == (H, W)
    assert m["gray_pixel_ratio"] > 0.02
    assert m["fragment_count"] >= 30
    assert m["closed_region_count"] >= 2  # 缺口被封閉
    assert 0 < m["black_pixel_ratio"] < 0.5
    assert set(res.steps) == {"resized", "gray", "binary", "cleaned", "closed", "final"}
    # 確定性：同輸入同參數結果一致
    res2 = lp.process(np.stack([img] * 3, -1), dict(SMALL))
    assert np.array_equal(res.ink, res2.ink) and res.metrics == res2.metrics


def test_process_file_outputs_1bit_png_with_dpi(tmp_path):
    src = tmp_path / "in.png"
    Image.fromarray(circles_with_gap(0)).save(src)
    dst = tmp_path / "out" / "x.png"
    res = lp.process_file(src, dst, {"target_width": 300, "target_height": 400, "line_width_px": 5})
    with Image.open(dst) as im:
        assert im.mode == "1"
        assert im.size == (300, 400)
        assert round(im.info["dpi"][0]) == 300
        back = np.asarray(im.convert("L")) < 128
    assert np.array_equal(back, res.ink)


def test_default_target_is_a4_300dpi():
    p = LineartParams()
    assert (p.target_width, p.target_height) == (2480, 3508)
    assert 10 <= p.line_width_px <= 12


def test_to_gray_flattens_4_channel_array_on_white():
    rgba = np.zeros((2, 2, 4), np.uint8)
    rgba[0, 0] = (0, 0, 0, 255)  # 不透明黑
    rgba[0, 1] = (0, 0, 0, 0)    # 全透明 → 白
    g = lp.to_gray(rgba)
    assert g[0, 0] == 0 and g[0, 1] == 255 and g[1, 0] == 255
