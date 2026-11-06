import cv2
import numpy as np
import pytest
from PIL import Image

from experiments import metrics


def test_image_metrics_from_file(tmp_path):
    img = np.full((400, 300), 255, np.uint8)
    cv2.rectangle(img, (50, 50), (250, 350), 0, 6)
    img[0:40, :] = 128
    p = tmp_path / "a.png"
    Image.fromarray(img).save(p)
    m = metrics.image_metrics(p, {"line_width_px": 6, "min_region_area": 100})
    assert set(metrics.METRIC_KEYS) <= set(m)
    assert m["closed_region_count"] == 1
    assert m["gray_pixel_ratio"] == pytest.approx(0.1, abs=0.01)


def _row(route, lineart, ok=True, **kw):
    base = {"cell_id": f"s__{route}__{lineart}__{kw.pop('seed', 1)}", "route": route, "lineart": lineart,
            "status": "ok" if ok else "error", "error": "" if ok else "OOM"}
    base.update(kw)
    return base


def test_summarize_groups_and_scores():
    rows = [
        _row("A", "p", seed=1, elapsed_s="10", closed_region_count="4"),
        _row("A", "p", seed=2, elapsed_s="20", closed_region_count="6"),
        _row("A", "p", ok=False, seed=3),
        _row("B", "p", seed=1, elapsed_s="5", closed_region_count="8"),
    ]
    scores = {"s__A__p__1": {"consistency_score": "4"}, "s__A__p__2": {"consistency_score": "2"},
              "s__B__p__1": {"consistency_score": ""}}
    s = {(x["route"], x["lineart"]): x for x in metrics.summarize(rows, scores=scores)}
    a = s[("A", "p")]
    assert (a["n"], a["n_ok"]) == (3, 2)
    assert a["success_rate"] == pytest.approx(0.667)
    assert a["elapsed_s_mean"] == 15 and a["closed_region_count_mean"] == 5
    assert a["consistency_mean"] == 3 and a["consistency_n"] == 2
    assert a["failures"] == "OOM"
    assert s[("B", "p")]["consistency_n"] == 0 and s[("B", "p")]["consistency_mean"] is None
    md = metrics.summary_markdown(list(s.values()))
    assert "| A | p | 2/3 | 3 ± 1.414 (n=2) | 15 ± 7.0711" in md and "失敗原因" in md


def test_load_scores_and_write_csv(tmp_path):
    p = tmp_path / "scores.csv"
    p.write_text("﻿cell_id,consistency_score,notes\nx,5,\"good, clean\"\n,3,\n", encoding="utf-8")
    s = metrics.load_scores(p)
    assert list(s) == ["x"] and s["x"]["notes"] == "good, clean"
    out = metrics.write_csv([{"a": 1, "b": None}], tmp_path / "o.csv")
    assert out.read_text().splitlines() == ["a,b", "1,"]
