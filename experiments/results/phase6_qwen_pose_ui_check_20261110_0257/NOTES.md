# Phase 6 real-GPU acceptance: pose precise/fast modes (2026-11-10)

## Setup

- ComfyUI 0.36.0 @ 8188 (the user had just restarted it), RTX 5080. GPU utilisation was 0% before starting.
- Branch `phase6-qwen-pose` (8e64546).
- To avoid touching the user's dev.sh (3000/8000, real `storage/app.db`), a separate backend ran on 8020 with scratch storage, plus `next dev` on 3020 (from a copy of the frontend).
- The full flow was clicked through with Playwright (chromium, `--disable-gpu`). Script: `accept_run.mjs`.
- Screenshots, PDF and the summary image are in `processed/` (not version-controlled). `processed/sheet.jpg` layout:
  - first column: reference character, and below it the 快速 pose on page 1;
  - row 1: backgrounds;
  - row 2: the 精準 poses used;
  - row 3: composites.

## Results

Book: A4, home printing. Character `a small round white bunny with long ears, wearing a red scarf`: 4 candidates took 38.7 s; the first was used.

| Page | Scene | Action | Background | Pose (精準) | Line art gaps → after autofix | Result | Page total |
|---|---|---|---|---|---|---|---|
| 1 | park | 揮手 (plus one 快速: 14.1 s) | 20.1 s | 33.8 s | 79 → 0 | Passed | 78.0 s |
| 2 | kitchen (moved right 10%, enlarged 5%) | 拿著東西 | 30.1 s | 31.9 s | 43 → 0 | Passed | 74.1 s |
| 3 | beach (moved left 10%) | 跳起來 | 26.5 s | 32.0 s | 37 → 0 | Passed | 69.3 s |

- **Default and badges:** the switch defaulted to 「精準」 on all three pages. On page 1 the history showed both 精準 and 快速 badges.
- **Provenance:** in the database, precise poses have `workflow_name = qwen_edit_pose_cutout` (version 052f9682f429) and request_json `pose_mode = precise`. The fast one is `sdxl_pose_cutout` / `fast`.
- **Engine time per image:**
  - 精準 29.4–30.8 s;
  - 快速 13.2 s;
  - background 18.0–26.6 s (6 s in Phase 5, see below);
  - composite 0.2 s.
- **QA and export:** all three pages had no gaps after autofix and passed. Pages 1 and 2 still show the 「太多過小的區域」 warning. The 3-page PDF exported (`%PDF`, 162 KB).
- **Phone width:** at 390 px wide, the compose, line art and QA tabs all scroll horizontally by 0 px.

## Observations

1. **Precise mode is much more consistent.** This run's reference character has a red headband on its head plus a knotted scarf. All three precise poses copy the face, headband, scarf knot and ear colour faithfully, and the actions are clear: waving, holding a tomato with both hands, jumping.
   The 快速 pose on the same page turned the headband into a big red head wrap, sat down instead of waving, and changed the face shape.
2. **The jumping image's detached ground shadow and motion lines did not end up in the composite.** The "keep only the largest piece" step works.
3. **Cost: model switching.** In this flow each page runs background (SDXL) → pose (Qwen). With 16 GB VRAM and 31 GB RAM, both model sets can't stay loaded, so ComfyUI reloads every time:
   - backgrounds went from 6 s to 18–27 s;
   - each page took about 70–78 s from new page to passed (Phase 5: about 30 s).
   To speed this up, do all the backgrounds first and then all the poses (fewer switches), or use 快速 for a pose.
4. Backgrounds are still busy in places (the park tree had 79 gaps), but autofix closed every gap.

## Problems found

None. No code changes.
