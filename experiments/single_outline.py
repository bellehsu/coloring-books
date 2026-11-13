"""#5 single-outline experiment (CPU only): compare line-art extraction methods on existing composites.

  (a) xdog            — current product (RENDER_VERSION 1)
  (b) xdog+dark T     — pixels with luma < T are filled solid before skeletonize, so a thick dark outline
                        skeletonizes to ONE centre line instead of two edge lines (T in 50/70/90)
  (c) hybrid          — archived experiments method (luma-led + xdog patches)

Usage: env -u PYTHONPATH PYTHONPATH=backend:. python experiments/single_outline.py <composites_dir_or_db_storage> <out_dir>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.services import lineart_post
from app.services.imageio import flatten_on_white
from app.services.lineart_extract import extract_xdog
from app.services.lineart_render import DETAIL_SIGMA, LineDetail
from app.services.qa import evaluate
from experiments.lineart_methods import extract as extract_archived

LINE_W = 11
ONLY: set[str] = set()
PAGES: set[str] = set()


def load_rgb(p: Path) -> np.ndarray:
    return np.asarray(flatten_on_white(Image.open(p)).convert("RGB"))


def dark_fill(rgb: np.ndarray, gray: np.ndarray, t: int) -> np.ndarray:
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = (luma < t).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))  # drop single-pixel noise
    out = gray.copy()
    out[dark > 0] = 0
    return out


def thin_dark_fill(rgb: np.ndarray, gray: np.ndarray, t: int, r: int) -> np.ndarray:
    """Fill only THIN dark strokes: dark minus its morphological opening with a disk of radius r.
    Thick dark areas (eyes, night sky) survive the opening, so they are left to xdog's outline."""
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = (luma < t).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    thick = cv2.morphologyEx(dark, cv2.MORPH_OPEN, k)
    thin = (dark > 0) & (thick == 0)
    out = gray.copy()
    out[thin] = 0
    return out


def stroke_fill(rgb: np.ndarray, gray: np.ndarray, t: int, r: int, keep: float) -> np.ndarray:
    """Like thin_dark_fill, but thick dark blobs whose max half-thickness < keep (stroke junctions)
    are filled too; only genuinely solid shapes (eyes, nose, night sky) keep xdog's outline."""
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = (luma < t).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    thick = cv2.morphologyEx(dark, cv2.MORPH_OPEN, k)
    dt = cv2.distanceTransform(dark, cv2.DIST_L2, 5)
    n, lab = cv2.connectedComponents(thick, connectivity=8)
    mx = np.zeros(n, np.float32)
    np.maximum.at(mx, lab.ravel(), dt.ravel())
    solid = (lab > 0) & (mx[lab] >= keep)
    out = gray.copy()
    out[(dark > 0) & ~solid] = 0
    return out


def methods(rgb: np.ndarray) -> dict[str, np.ndarray]:
    xd = extract_xdog(rgb, DETAIL_SIGMA[LineDetail.STANDARD])
    out = {"a_xdog": xd}
    for t in (50, 70, 90):
        out[f"b_dark{t}"] = dark_fill(rgb, xd, t)
    out["c_hybrid"] = extract_archived(rgb, {"method": "hybrid"})
    for r in (4, 6, 8):
        out[f"d_thin70_r{r}"] = thin_dark_fill(rgb, xd, 70, r)
    for keep in (11, 13):
        out[f"e_stroke70_r8_k{keep}"] = stroke_fill(rgb, xd, 70, 8, keep)
    out["e_stroke70_r10_k15"] = stroke_fill(rgb, xd, 70, 10, 15)
    out["e_stroke70_r10_k13"] = stroke_fill(rgb, xd, 70, 10, 13)
    out["e_stroke70_r12_k14"] = stroke_fill(rgb, xd, 70, 12, 14)
    if ONLY:
        out = {k: v for k, v in out.items() if k in ONLY}
    return out


def main(src: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    content_px = (2244, 3272)  # A4 家用 content box (same as the demo book)
    files = sorted(src.glob("*.png"))
    if PAGES:
        files = [f for f in files if f.stem in PAGES]
    rows = []
    for f in files:
        rgb = load_rgb(f)
        rec = {"file": f.name}
        for name, gray in methods(rgb).items():
            t0 = time.time()
            ink = lineart_post.render_ink(gray, content_px, LINE_W)
            qa = evaluate(ink, LINE_W)
            rec[name] = {"gap_count": qa.gap_count, "tiny_regions": getattr(qa, "tiny_region_count", None),
                         "black_fill": round(float(ink.mean()), 4), "ms": int((time.time() - t0) * 1000)}
            Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).save(out / f"{f.stem}__{name}.png", optimize=True)
        rows.append(rec)
        print(f.name, {k: v["gap_count"] for k, v in rec.items() if k != "file"}, flush=True)
    (out / "metrics.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 3:
        ONLY.update(sys.argv[3].split(","))
    if len(sys.argv) > 4:
        PAGES.update(sys.argv[4].split(","))
    main(Path(sys.argv[1]), Path(sys.argv[2]))
