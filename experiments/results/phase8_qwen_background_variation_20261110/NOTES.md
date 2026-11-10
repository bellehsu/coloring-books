# Qwen background composition-variation calibration (Phase 8 Task 3, 2026-11-10)

Problem: in the phase7 spike, `qwen_background` gave almost the same composition for different seeds, so 「再產生一張」 barely changed anything. Spec §3.5 asks for calibrating a "composition variation" phrase list. One phrase is picked at random per image and written into the prompt.

## Setup
- ComfyUI 0.36.0 @ 8188, RTX 5080. GPU idle, `/queue` empty. Models were unloaded with `POST /free` after the run.
- The images go through the production workflow `comfy_workflows/qwen_background` (`WorkflowRegistry.build_prompt`) and `prompts.qwen_background_prompt(scene, variation)`.
- Same 6 scenes as the spike, seeds 101 and 202, 864×1216. Line-art metrics are also as in the spike: `render_base(img, (2244, 3272), STANDARD, 11)` → `qa.evaluate`.
- Each version is 12 images. In the variation versions, s101 uses phrase i of the list and s202 uses phrase i+1, so the two images of a scene always get **different** phrases (forced, not two independent random draws; independent draws would repeat a phrase 25% of the time with 4 phrases). Since the Phase 8 final fix, the product guarantees the same thing: 「再產生一張」 for a simple background excludes the phrase of the page's most recent simple background (`pick_background_variation(rng, exclude=...)`), so this calibration measures what users get on regenerate.
- Script: `run.py`. Per-image data: `results.csv`. Images and contact sheets (`processed/sheet.jpg`, `sheet_lineart.jpg`) are not version-controlled.

| Version | Variation phrases | Avg. gaps | Avg. tiny regions | Time per image | Same-scene pair difference* | People/animals | Visual |
|---|---|---|---|---|---|---|---|
| V0 | none (= spike QA1) | 9.5 | 82.3 | 15.3 s | 0.111 | 0/12 | The two images are almost identical in composition |
| VA | Viewpoint: wide view from the front / a little to the left / a little to the right / close view, objects bigger | 9.8 | **109.1** ✗ | 18.5 s | 0.120 | 0/12 | Small change (mostly zoom) |
| **VB** | **Main-object position: left / right / middle, low angle / at the back, open space in front** | **9.8** | **73.2** | 19.8 s | 0.112 | 0/12 | **Clearly different**: swing set left vs right, beach umbrella and sandcastle swap sides, kitchen and classroom laid out differently, forest path composition different |
| VC | Viewpoint + distance mixed | **12.8** ✗ | 80.1 | 29.0 s† | 0.120 | 0/12 | Small change |

\* Mean absolute difference between the two line-art images after downscaling to 96×136. This number barely separates the versions (the two images in V0 already differ in line positions), so it is only a reference; whether they differ was judged by eye on the contact sheet.
† VC includes one 53 s outlier (classroom s202); the other images are about 17–20 s.

- The V0 metrics match the spike's QA1 exactly (9.5 / 82.3), confirming that the production workflow is the same as the spike graph.

## Conclusion
- VB passes the spec §3.5 criteria (gaps ≤ 11.5, tiny regions ≤ 99, no people or animals, clearly different by eye) and is adopted as `prompts.QWEN_BACKGROUND_VARIATIONS`.
- VA fails on tiny regions; VC fails on gaps.
- Cost: about 4 s more per image (the prompt is longer and the first encode is not cached).
- VB's "The main objects are on the right side" leaves about half of the page empty in two images (kitchen s101, forest s101: objects pushed to the right edge, left half mostly blank; see `results.csv`). The "left side" images (park s101, beach s202, classroom s101) do not show this as strongly. Empty space suits children colouring in (and leaves room for the character), but if it reads as too empty in acceptance, first reword the right-side phrase (e.g. "The main objects are on the right side, filling most of the page") or drop it; "open space in front" and "low angle" are not implicated by these images.
- People/animals is 0/12 for VB taken literally, but VB beach s101 draws a smiling face on the sandcastle (anthropomorphic object; the prompt says "no characters"). Not a reason to change the list; watch for faces on objects during acceptance.
