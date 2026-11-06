import csv

import cv2
import numpy as np

from app.services.qa import evaluate
from experiments.qa_calibrate import autofix, main


def ring_with_gap(gap: int = 20) -> np.ndarray:
    img = np.full((900, 900), 255, np.uint8)
    cv2.circle(img, (450, 450), 300, 0, 11)
    img[450 - gap // 2: 450 + gap // 2, 740:760] = 255
    return img < 128


def test_autofix_closes_ring_gap():
    ink = ring_with_gap()
    report = evaluate(ink, 11)
    assert report.gap_count == 1
    fixed, after, strokes = autofix(ink, report, 11)
    assert after.gap_count == 0 and len(strokes) == 1
    assert not ink[450, 750] and fixed[450, 750]  # 原圖不被修改


def test_autofix_noop_without_gaps():
    ink = ring_with_gap(0)
    _, after, strokes = autofix(ink, evaluate(ink, 11), 11)
    assert after.gap_count == 0 and strokes == []


def test_main_writes_csv_and_overlays(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    img = np.full((1216, 832, 3), 255, np.uint8)
    cv2.rectangle(img, (150, 200), (680, 1000), (0, 0, 0), 6)
    cv2.imwrite(str(src / "box.png"), img)
    out = tmp_path / "out"
    main([str(src), str(out), "--paper", "a5"])
    rows = list(csv.DictReader((out / "results.csv").open(encoding="utf-8")))
    assert [r["name"] for r in rows] == ["box"] and int(rows[0]["gap_count"]) == 0
    assert (out / "overlay" / "box.png").is_file()
