# Phase 14 #5 — single outline experiment (CPU only, 2026-11-13)

Script: `experiments/single_outline.py` (`env -u PYTHONPATH PYTHONPATH=backend:. python experiments/single_outline.py <in> <out> [methods] [pages]`).
Inputs: `input/` = 20 selected composites of the README demo book (小狐狸的一天); `input_bear/` = 20 composites of the
user's book 小熊尋找彩虹 (read-only copy from R2). Inputs and full outputs are git-ignored (size / user data); the contact
sheets and metrics are kept.

## Problem
xdog draws both edges of a thick black outline; skeletonize + dilate then prints two parallel lines with "beads".
The generation prompts ask for "thick clean black outlines", so every character has such outlines (worse in 小熊).

## Methods (gap_count = QA leak count, lower is better; see out/metrics*.json)
| method | idea | verdict |
|---|---|---|
| a_xdog | current (RENDER_VERSION 1) | double outline + beads |
| b_dark50/70/90 | force every luma<T pixel to ink before render_ink | single outline, but eyes → 「＋」, night sky → polygon mesh (compare_full_pages.png p20) |
| c_hybrid | archived experiments method | tube-like double outlines everywhere |
| d_thin70_r4/6/8 | fill dark − opening(dark, r) | eyes kept, junctions become beads |
| **e_stroke70_r10_k13** | d + fill thick components whose max distance-transform < keep (junctions); solid shapes (dt ≥ keep: eyes, nose, sky) keep xdog's outline | **chosen** |

Sheets: `compare*_character_closeup.png`, `compare*_full_pages.png` (demo), `compare4_fox_*.png` (r8 vs r10 on demo),
`compare_bear_full.png`, `compare_bear_levels.png` (小熊 p7: r8 leaves double lines, r10 merges them).

Gap counts, demo p01/p03/p04/p11/p17/p20: a 58/–/–/–/–/30 … e_r10_k13 42/21/25/25/18/26.
Bear book (20 pages): a total 421 → e_r8_k13 387; r10 equal or better on thick pages (b07 36 → 25).

Stroke thickness (half-width at skeleton, luma<70, 832 px composites): typical outline 5–7.5 px, eyes ≈ 13–14 px,
so r = 10 (strokes up to ~20 px wide merge) and keep = 13 separate strokes from solid shapes.

## Decision (user, 2026-11-13)
- Automatic (no 細／一般／粗 control): one fixed setting r=10, keep=13 at 832 px width (scaled with width).
- Applies only to new / redrawn pages (per-page `outline_fill` flag); existing pages keep today's line art.
- Also change generation prompts toward thinner outlines (#8) — wording calibrated on GPU later.
Known minor artifact: dark-region rims (night-sky edge) may leave a few short dashes.
