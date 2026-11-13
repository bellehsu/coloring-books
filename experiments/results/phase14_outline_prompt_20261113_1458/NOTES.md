# Phase 14 #8 — OUTLINE_PHRASE calibration (GPU, 2026-11-13)

Script: `run.py` (run | sheet | free). ComfyUI 0.36.0 on 8188, Qwen-Image-Edit-2511 Q4_K_M + Lightning 4 steps.
Matrix: 2 characters (fox, bear) × 3 wordings × {character, 2 poses, 1 background}, fixed seeds (char 1001, pose 101,
bg 101). Metrics: outline half-width (skeleton of luma<70, distance transform, 832 px scale) p75/p90; QA gap_count of the
product line art with outline_fill=True. `results.csv` has every row; contact sheets in `sheets/` (color | line art).

| wording | bear half-width p75 (char/pose/pose) | fox p75 | line art |
|---|---|---|---|
| thick (old) | 10.2 / 9.0 / 8.6 | 8.8 / 8.8 / 7.2 | blobs at bear ears/neck |
| thin | 8.0 / 6.4 / 6.6 | 7.4 / 7.4 / 6.4 | cleanest |
| medium | 9.2 / 7.6 / 8.0 | 8.2 / 8.4 / 7.0 | fur tufts; one pose added a pencil |

Timing: 18–48 s per image. The first run was killed by host RAM pressure after 5 images (ComfyUI holds a lot of RAM);
resumed from metrics.json.
**User pick: medium** → `OUTLINE_PHRASE = "clean medium-weight black outlines"` (commit aad0dfa).
