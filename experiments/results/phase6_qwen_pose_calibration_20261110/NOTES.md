# Precise-mode (Qwen) prompt calibration (2026-11-10)

Purpose: before wiring `qwen_edit_pose_cutout` (Phase 6 Task 1) into the website, tune the prompt.

Setup:
- ComfyUI 0.36.0 @ 8188, RTX 5080. The GPU was confirmed idle before running.
- Run through the real workflow (`WorkflowRegistry.build_prompt`).
- Same rabbit reference as the phase5 comparison experiment, the 6 action buttons, seeds 101 and 202.
- Masks first go through "keep only the largest connected component" (the same method as Task 2's `largest_component`). The line-art metrics come from the cutout on a white background → `render_base` (A4 content area, line width 11) → `qa.evaluate`.
- Script: `run.py`. Per-image metrics are in `results.csv`. Images and contact sheets are in `processed/` (not version-controlled):
  - `sheet_cut.jpg` covers V1–V3;
  - `sheet_cut_V4_V5.jpg` covers V4 and V5;
  - `sheet_cut_V1_V6_V7.jpg` covers V1, V6 and V7.

## Versions and results (12 images each)

| Version | Prompt change | Time s | Gaps | Small regions | Masks with several pieces before LC | Consistency* | Action* | Notes |
|---|---|---|---|---|---|---|---|---|
| V1 | phase5 experiment wording (baseline) | 21.2 | 15.2 | 36.0 | 3 | 4.0 | 5.0 | Draped scarf as in the reference; ground shadow in 5/12; both seeds almost identical |
| V2 | Scarf only around the neck, white arms, no sleeves, nothing under the feet | 20.7 | 9.1 | 30.5 | 1 | 3.6 | 4.2 | Scarf shortened to a little neck scarf (unlike the reference); **neither sitting image sits**; ears upright in one waving image |
| V3 | V2 + facing left/right | 25.2 | 7.2 | 27.2 | 1 | 3.4 | 4.2 | More variety, but the face and ears drift more on s202; sitting still fails |
| V4 | Scarf "stays around the neck as in the reference image", no shadow | 20.5 | 8.5 | 28.1 | 0 | 3.5 | 4.2 | Scarf still shortened; **sitting still standing**; ears upright when waving |
| V5 | V4 + facing left/right | 24.1 | 4.2 | 34.2 | 2 | 3.3 | 4.2 | Same as V4, with more drift |
| **V6** | **V1 + "no shadow, no cast shadow"; 坐著 changed to "sitting on the ground"** | 19.8 | 15.4 | 33.1 | **0** | **4.1** | **5.0** | Closest to the reference; shadow only in 1–2 sitting images; seeds still almost identical |
| V7 | V6 + facing left/right | 23.9 | 11.4 | 36.6 | 2 | 3.6 | 5.0 | More variety, but "facing slightly to the right" turns the ears upright (waving, jumping, running) |

\* Scored by eye, out of 5. The 12 images are judged together, against the reference image's face, ear shape and colour, the draped scarf, and proportions.

## Conclusions

1. **Use V6 as the default** (`prompts.QWEN_POSE_TEMPLATE`): V1's wording plus "no shadow". Pass criteria:
   - consistency 4.1 ≥ 4.0;
   - action 5.0 ≥ 4.5;
   - masks 12/12 clean after LC (none had several pieces even before LC);
   - shadows entering the line art ≤ 2/12 (only the sitting pose).
2. **The "red sleeves" criterion is waived** (it was the author's own call, not something the user raised). The red cloth on the arms is the reference image's long scarf draped over the shoulders and arms, so it is actually faithful to the reference. Every phrasing that tried to remove it (V2–V5) shortened the scarf, stopped the sitting pose from sitting and made the ears stand up, which hurt consistency more.
3. **No random variation** (`QWEN_POSE_VARIATIONS = ()`): adding a facing direction does vary the images, but consistency drops by about 0.5. The cost is that 「再產生一張」 changes very little (the two seeds are nearly identical); this goes in the end-of-phase report.
4. **The 坐著 preset changes from "sitting" to "sitting on the ground"**: it was tested with V6 and is more explicit (V1 also sat with "sitting", but V2–V5 show that wording can easily turn into a standing pose). The change goes into the frontend's `lib/actions.ts` (Task 5).
5. Time: about 20 s per image without variation, 24 s with it (excluding model reloads).
