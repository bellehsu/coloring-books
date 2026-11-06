"""QA 門檻校正：對一批彩圖跑「線稿 → QA → 自動補線」，輸出指標 CSV 與標記圖。

用法：
  python -m experiments.qa_calibrate <圖片資料夾> <輸出資料夾> [--paper a4 --mode home --detail standard --width 11]

輸出：
  results.csv           每張圖：缺口數、近缺口數、線頭數、各項指標、自動補線後缺口數、耗時
  overlay/<名稱>.png     半尺寸標記圖：紅圈＝缺口、藍線＝自動補線線段
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2
import numpy as np

from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.lineart_post import load_image
from app.services.lineart_render import LineDetail, Stroke, apply_strokes, render_base
from app.services.qa import QAReport, evaluate

AUTOFIX_PASSES = 3
FIELDS = ("name", "gap_count", "near_miss_count", "dangling_count", "tiny_region_count", "fragment_count",
          "black_fill_ratio", "closed_region_count", "gaps_after_autofix", "autofix_strokes", "errors",
          "warnings", "qa_s")


def autofix(ink: np.ndarray, report: QAReport, line_width_px: int,
            passes: int = AUTOFIX_PASSES) -> tuple[np.ndarray, QAReport, list[Stroke]]:
    """重複補上缺口線段，直到沒有缺口或缺口數不再減少。"""
    added: list[Stroke] = []
    for _ in range(passes):
        if report.gap_count == 0:
            break
        strokes = [Stroke("draw", float(line_width_px), (e, q)) for e, q in report.bridges]
        fixed = apply_strokes(ink, strokes)
        new = evaluate(fixed, line_width_px)
        if new.gap_count >= report.gap_count:
            break
        ink, report = fixed, new
        added += strokes
    return ink, report, added


def overlay(ink: np.ndarray, report: QAReport, strokes: list[Stroke]) -> np.ndarray:
    h, w = ink.shape
    img = cv2.cvtColor(np.where(ink, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    for s in strokes:
        (x1, y1), (x2, y2) = s.points
        cv2.line(img, (round(x1 * (w - 1)), round(y1 * (h - 1))), (round(x2 * (w - 1)), round(y2 * (h - 1))),
                 (255, 128, 0), 6)
    for x, y in report.gaps:
        cv2.circle(img, (round(x * w), round(y * h)), 40, (0, 0, 255), 6)
    return cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)


def calibrate(src: Path, out: Path, paper: PaperSize, mode: PrintMode, detail: LineDetail,
              width: int) -> list[dict]:
    content_px = compute_layout(paper, mode).content_px
    (out / "overlay").mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(src.glob("*.png")):
        ink, _ = render_base(load_image(f), content_px, detail, width)
        t = time.perf_counter()
        report = evaluate(ink, width)
        qa_s = time.perf_counter() - t
        _, after, strokes = autofix(ink, report, width)
        m = report.metrics
        rows.append({
            "name": f.stem, "gap_count": report.gap_count, "near_miss_count": report.near_miss_count,
            "dangling_count": report.dangling_count, "tiny_region_count": m["tiny_region_count"],
            "fragment_count": m["fragment_count"], "black_fill_ratio": round(m["black_fill_ratio"], 5),
            "closed_region_count": m["closed_region_count"], "gaps_after_autofix": after.gap_count,
            "autofix_strokes": len(strokes), "errors": " ".join(report.errors),
            "warnings": " ".join(report.warnings), "qa_s": round(qa_s, 3),
        })
        cv2.imwrite(str(out / "overlay" / f"{f.stem}.png"), overlay(ink, report, strokes))
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, FIELDS)
        wr.writeheader()
        wr.writerows(rows)
    return rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--paper", default="a4", choices=[p.value for p in PaperSize])
    ap.add_argument("--mode", default="home", choices=[m.value for m in PrintMode])
    ap.add_argument("--detail", default="standard", choices=[d.value for d in LineDetail])
    ap.add_argument("--width", type=int, default=11)
    a = ap.parse_args(argv)
    rows = calibrate(a.src, a.out, PaperSize(a.paper), PrintMode(a.mode), LineDetail(a.detail), a.width)
    print(f"{len(rows)} 張，結果：{a.out / 'results.csv'}")


if __name__ == "__main__":
    main()
