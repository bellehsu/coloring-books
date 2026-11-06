"""experiments/pose_sketches：草圖尺寸、白底黑線、每個場景不同。"""

import cv2
import numpy as np

from experiments import pose_sketches as ps


def test_make_all(tmp_path):
    out = ps.make_all(tmp_path)
    assert set(ps.SCENES) <= set(out)
    imgs = {k: cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) for k, p in out.items()}
    for img in imgs.values():
        assert img.shape == (ps.H, ps.W)
        assert np.median(img) == 255  # 白底
        assert 0.01 < (img < 128).mean() < 0.2  # 有線，但不是大片黑
        assert img[0, ps.W // 2] == 255 or img[ps.H // 2, 0] == 255
    a, b, c = (imgs[k] for k in ps.SCENES)
    assert not np.array_equal(a, b) and not np.array_equal(b, c)


def test_committed_samples_match_generator(tmp_path):
    """experiments/samples/pose 裡的檔案必須等於產生器目前的輸出（改了產生器要重新產生）。"""
    from tests.conftest import REPO
    out = ps.make_all(tmp_path)
    for k, p in out.items():
        committed = cv2.imread(str(REPO / "experiments" / "samples" / "pose" / f"{k}.png"), cv2.IMREAD_GRAYSCALE)
        assert committed is not None, k
        assert np.array_equal(committed, cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)), k


def test_props_only_variant_has_no_character(tmp_path):
    out = ps.make_all(tmp_path)
    assert set(out) == set(ps.SCENES) | {f"{s}_props" for s in ps.SCENES}
    heads = {"park_swing": (416, 450), "kitchen_baking": (600, 420), "bedroom_reading": (330, 520)}
    for s, (cx, cy) in heads.items():
        full = cv2.imread(str(out[s]), cv2.IMREAD_GRAYSCALE) < 128
        props = cv2.imread(str(out[f"{s}_props"]), cv2.IMREAD_GRAYSCALE) < 128
        assert props.sum() < full.sum() and props.any()
        assert not (props & ~full).any()  # 道具版是完整版的子集
        r = 140  # 頭部外接範圍內沒有任何線（頭半徑 ~125 + 線寬）
        head_full = full[cy - r:cy + r, cx - r:cx + r]
        head_props = props[cy - r:cy + r, cx - r:cx + r]
        assert head_full.sum() > 3 * head_props.sum()
