import json

import cv2
import numpy as np

from app.services.qa import (
    QA_MESSAGES,
    QAReport,
    QAThresholds,
    evaluate,
    find_candidates,
    find_endpoints,
    find_gaps,
    find_leaks,
)

SIZE = 600
CENTER = 300
RADIUS = 200
LW = 11


def blank() -> np.ndarray:
    return np.zeros((SIZE, SIZE), np.uint8)


def ring(gap_px: int) -> np.ndarray:
    """600×600 白底，線寬 11 的圓；右側挖出 gap_px 寬（垂直方向）的缺口。"""
    img = blank()
    cv2.circle(img, (CENTER, CENTER), RADIUS, 1, LW)
    if gap_px > 0:
        y0 = CENTER - gap_px // 2
        img[y0:y0 + gap_px, CENTER + RADIUS - 2 * LW:CENTER + RADIUS + 2 * LW] = 0
    return img.astype(bool)


def test_closed_shape_has_no_gaps():
    r = evaluate(ring(0), LW)
    assert r.gap_count == 0 and r.errors == [] and r.dangling_count == 0


def test_small_gap_flagged_once_at_gap_location():
    r = evaluate(ring(20), LW)
    assert r.gap_count == 1 and r.errors == ["gaps"] and len(r.gaps) == 1 and r.near_miss_count == 1
    right_edge_rel = (CENTER + RADIUS) / SIZE
    x, y = r.gaps[0]
    assert abs(x - right_edge_rel) < 0.05 and abs(y - 0.5) < 0.05


def test_wide_opening_is_not_a_gap():
    r = evaluate(ring(120), LW)  # 大於 max_gap_px 是刻意的開口
    assert r.gap_count == 0 and r.near_miss_count == 0 and r.errors == [] and r.dangling_count == 2


def test_line_stopping_short_of_other_line_that_would_close_a_region_is_gap():
    # U 形（左、下、右三邊；右邊往上多伸出一截），上邊從左上角往右畫，停在離右邊 15 px 處
    img = blank()
    cv2.line(img, (150, 150), (150, 450), 1, LW)
    cv2.line(img, (150, 450), (450, 450), 1, LW)
    cv2.line(img, (450, 100), (450, 450), 1, LW)  # 右邊佔 x 444..456
    cv2.line(img, (150, 150), (422, 150), 1, LW)  # 上邊圓頭在 x 428 → 離右邊 15 px
    ink = img.astype(bool)
    assert ink[150, 428] and not ink[150, 429:444].any() and ink[150, 444]
    r = evaluate(ink, LW)
    assert r.gap_count == 1 and r.near_miss_count == 1 and r.errors == ["gaps"]
    x, y = r.gaps[0]
    assert abs(x * SIZE - 436) < 10 and abs(y * SIZE - 150) < 10


def test_strand_nearly_touching_outline_is_near_miss_not_gap():
    # 封閉的圓裡有一條自由端的髮絲，末端離圓 15 px：沒有封住任何區域 → 不會漏色
    img = blank()
    cv2.circle(img, (CENTER, CENTER), RADIUS, 1, LW)  # 圓內緣約 x = 300 + 194
    cv2.line(img, (300, 300), (472, 300), 1, LW)  # 圓頭在 x 478 → 離圓內緣約 15 px
    ink = img.astype(bool)
    assert ink[300, 478] and not ink[300, 479:494].any() and ink[300, 494]
    r = evaluate(ink, LW)
    assert r.near_miss_count >= 1 and r.gap_count == 0 and r.gaps == [] and r.errors == []
    assert r.metrics["near_miss_count"] == r.near_miss_count


def test_free_line_end_is_dangling_not_gap():
    img = blank()
    cv2.line(img, (250, 300), (350, 300), 1, LW)
    r = evaluate(img.astype(bool), LW)
    assert r.dangling_count == 2 and r.gap_count == 0 and r.errors == []


def test_border_endpoints_ignored():
    img = blank()
    cv2.line(img, (0, 300), (SIZE - 1, 300), 1, LW)
    skel, endpoints = find_endpoints(img.astype(bool))
    assert skel.dtype == bool and skel.shape == (SIZE, SIZE) and skel[300, 300]
    assert endpoints == []
    assert evaluate(img.astype(bool), LW).dangling_count == 0


def test_endpoints_are_xy_tuples():
    img = blank()
    cv2.line(img, (250, 300), (350, 300), 1, LW)
    _, endpoints = find_endpoints(img.astype(bool))
    assert len(endpoints) == 2
    xs = sorted(x for x, _ in endpoints)
    assert abs(xs[0] - 250) <= 6 and abs(xs[1] - 350) <= 6
    assert all(abs(y - 300) <= 1 for _, y in endpoints)


def test_isolated_dot_is_not_an_endpoint():
    img = blank()
    cv2.circle(img, (300, 300), 4, 1, -1)  # 實心小點 → 骨架只剩孤立像素
    _, endpoints = find_endpoints(img.astype(bool))
    assert endpoints == []


def test_find_gaps_merges_facing_endpoints():
    skel = np.zeros((200, 200), bool)
    skel[100, 20:90] = True
    skel[100, 110:180] = True  # 兩條線頭相距 20 px
    gaps = find_gaps(skel, [(20, 100), (89, 100), (110, 100), (179, 100)], 40)
    assert gaps == [(99, 100)] or gaps == [(100, 100)]


def test_find_gaps_ignores_own_line_within_path_limit():
    skel = np.zeros((200, 200), bool)
    skel[100, 20:180] = True
    assert find_gaps(skel, [(20, 100), (179, 100)], 40) == []


def test_find_candidates_returns_endpoint_pairs():
    skel = np.zeros((200, 200), bool)
    skel[100, 20:90] = True
    skel[100, 110:180] = True
    pairs = find_candidates(skel, [(20, 100), (89, 100), (110, 100), (179, 100)], 40)
    assert pairs == [((89, 100), (110, 100)), ((110, 100), (89, 100))]


def test_find_leaks_requires_both_sides_to_be_large_regions():
    ink = ring(20)
    skel, endpoints = find_endpoints(ink)
    pairs = find_candidates(skel, endpoints, 40)
    assert len(pairs) == 2
    assert len(find_leaks(ink, pairs, LW, 3000, 40)) == 1
    # 圓內面積約 12 萬 px：門檻調到 20 萬時圓內不算「可塗色區域」→ 不算漏色
    assert find_leaks(ink, pairs, LW, 200_000, 40) == []


def test_warnings_from_metrics():
    img = blank()
    img[200:400, 200:400] = 1
    r = evaluate(img.astype(bool), LW)
    assert "black_fill" in r.warnings and "black_fill" not in r.errors
    assert r.metrics["black_fill_ratio"] > 0.005


def test_metrics_contents():
    r = evaluate(ring(20), LW)
    for k in ("region_count", "closed_region_count", "tiny_region_count", "fragment_count",
              "black_fill_ratio", "gap_count", "dangling_count", "near_miss_count"):
        assert k in r.metrics
    assert r.metrics["gap_count"] == r.gap_count == 1
    assert r.metrics["dangling_count"] == r.dangling_count == 2
    assert r.metrics["near_miss_count"] == r.near_miss_count == 1


def test_thresholds_defaults_and_custom():
    t = QAThresholds()
    assert (t.max_gap_px, t.border_px, t.gap_error_min) == (40, 12, 1)
    assert (t.black_fill_warn, t.tiny_region_warn, t.fragment_warn) == (0.005, 150, 80)
    # 調高 gap_error_min：缺口仍被偵測，但不算錯誤
    r = evaluate(ring(20), LW, QAThresholds(gap_error_min=2))
    assert r.gap_count == 1 and r.errors == []


def test_messages():
    assert QA_MESSAGES["gaps"] == "線條有缺口，塗色會漏出去，需要修線"
    assert set(QA_MESSAGES) == {"gaps", "black_fill", "tiny_regions", "fragments"}


def test_report_roundtrip():
    r = evaluate(ring(20), LW)
    d = r.to_dict()
    assert d["near_miss_count"] == 1
    assert QAReport.from_dict(json.loads(json.dumps(d))) == r
    assert QAReport.from_dict(d) == r
