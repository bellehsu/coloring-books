"""autofix：survives() 只比對筆畫外框的子陣列，結果要與整頁版一致（C14）。"""

import numpy as np

from app.services.autofix import survives
from app.services.lineart_render import Stroke
from tests.test_lineart_render import legacy_apply_strokes, random_strokes


def legacy_survives(erased, pre, stroke):
    return erased is None or not (legacy_apply_strokes(np.zeros_like(pre), [stroke]) & erased).any()


def test_survives_matches_legacy():
    rng = np.random.default_rng(1234)
    w, h = 2244, 3272
    pre = np.zeros((h, w), bool)
    user = random_strokes(rng, 60)
    erased = ~legacy_apply_strokes(np.ones_like(pre), user)
    results = []
    for s0 in random_strokes(rng, 200):
        s = Stroke("draw", s0.width_px, s0.points)  # 補線都是 draw
        a, b = survives(erased, s), legacy_survives(erased, pre, s)
        assert a == b
        results.append(a)
    assert True in results and False in results  # 兩種結果都有被測到
    assert survives(None, random_strokes(rng, 1)[0]) is True
