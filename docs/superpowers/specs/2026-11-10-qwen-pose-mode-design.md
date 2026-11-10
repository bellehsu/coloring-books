# Character pose: precise (Qwen) and fast (SDXL) modes — design

Date: 2026-11-10. Branch: `phase6-qwen-pose`. Builds on the Phase 5 compose flow (`2026-11-09-page-compose-flow-design.md`).

## 1. Why

In the 角色 step, the book's character is redrawn in a new action for every page. With the current workflow (`sdxl_pose_cutout`,
SDXL + IP-Adapter 0.55) the character drifts: the scarf ends up on the head and the ears change. The actions are also barely drawn: wave and hold
both look like the reference's standing pose. The comparison experiment `experiments/results/phase5_pose_consistency_20261110_0739/`
found that Qwen-Image-Edit-2511, which **edits the reference image**, is much better:

| | Consistency | Action | Line-art gaps | Time |
|---|---|---|---|---|
| Current SDXL | 2.5 | 1.8 | 24 | ~8 s |
| Qwen 2511 GGUF + Lightning | 4.0 | 5.0 | 16 | ~25–30 s |

## 2. User decisions (2026-11-10)

- **Keep both and allow switching**: 「精準」 = Qwen and 「快速」 = the current SDXL.
- **Default to precise (Qwen).**
- **The switch sits on the character tab and remembers the last choice.** It goes above the generate button, and this browser remembers it. Each history thumbnail shows which mode produced it.

## 3. Goals, non-goals, anti-goals

**Goals**
- In precise mode the character stays consistent with the reference image (face, ear shape and colour, scarf on the neck, proportions), and the action is clearly drawn.
- Switching modes only changes how the pose is generated. Background removal, compose, line art, QA and export behave exactly as now.
- Each generation records which workflow was used (name, version hash, models, params), satisfying the provenance rule in CLAUDE.md.

**Non-goals**
- No change to background generation (still SDXL).
- No change to the character studio (character candidates still use `sdxl_text2img`).
- No fp8 Qwen (20.5 GB is more than 16 GB VRAM, and system RAM is only 31 GB). Only the GGUF Q4_K_M build.
- No OpenPose (the experiment showed sitting poses fail and a second pair of ears appears).
- Out of scope: vehicles series, captions, theme packs (to be discussed separately).

**Anti-goals (a failure even if it technically works)**
- Precise mode produces a ground shadow, or a red scarf that becomes sleeves or a cape, and it gets pasted into the background and becomes a closed region in the line art.
- If the precise mode's model or node is missing, the whole service must not crash, and the user must not end up with a mode that just fails silently.

## 4. Behaviour

### 4.1 Character tab (PosePanel)
- Above the generate button sits a segmented switch 「精準｜快速」 with a hint below it: 精準 「約 30 秒，角色最像、動作最準」, 快速 「約 10 秒，可能和角色有落差」.
- Default is 精準. After switching, this browser remembers the choice (`localStorage`, read and written inside try/catch; with no stored value, use 精準).
- The generate button and action presets are unchanged. Generating sends the current mode.
- If the backend reports that precise mode is unavailable (models or nodes missing), 精準 is disabled with an explanation, and the switch automatically falls back to 快速.

### 4.2 History tray
- Each pose thumbnail shows a small 「精準」 or 「快速」 badge, derived from the generation's workflow. Old Phase 5 pose generations count as 快速.

### 4.3 Background removal mask
- For pose generations in either mode, keep only the **largest connected component** of the mask when it is stored. This stops a detached shadow or stray bits from becoming a second piece.
- This is a deterministic step in the backend (it can sit in the worker's step that stores the mask, or in a function under `services/compose.py`) and needs unit tests.

## 5. Precise-mode workflow

New folder `comfy_workflows/qwen_edit_pose_cutout/` (`workflow_api.json` + `manifest.yaml`). Node layout follows the experiment's `run.py` (`wf_qwen()` + `bg_nodes()`), with every node located by `_meta.title`:

- Models:
  - `unet/qwen-image-edit-2511-Q4_K_M.gguf` (`UnetLoaderGGUF`, needs ComfyUI-GGUF, already installed locally);
  - `qwen_2.5_vl_7b_fp8_scaled` (`CLIPLoader` type `qwen_image`);
  - `qwen_image_vae`;
  - `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16` (`LoraLoaderModelOnly`);
  - `background_removal/birefnet.safetensors`.
- Chain: UNET → `ModelSamplingAuraFlow` shift 3.1 → `CFGNorm` 1.0 → LoRA 1.0. `TextEncodeQwenImageEditPlus` takes the reference image. Then `FluxKontextMultiReferenceLatentMethod` `index_timestep_zero`, `EmptySD3LatentImage` 832×1216, and KSampler with 4 steps, cfg 1, euler/simple. Then BiRefNet removes the background, outputting OUTPUT and MASK.
- The manifest whitelist covers: prompt, character reference image, seed, width/height (multiples of 16), steps, cfg, LoRA strength. The model list lets preflight check it.
- The prompt is instruction-style, added in `prompts.py`. Starting point from the experiment:
  `Make this exact character {action}. Keep the same character design, proportions, colors, scarf and ears unchanged. Full body, centered, plain pure white background, children's picture book illustration, flat colors, thick clean black outlines.`
  The negative prompt has no effect at cfg 1, so leave it empty.
- The character description is not included (the edit model reads appearance from the image). This is the builder's call, to be decided by calibration.

## 6. Data and API

- `POST /pages/{id}/poses` gains `mode: "precise" | "fast"` (optional, default `"precise"`). Other values return 422.
- When the precise workflow fails preflight (missing node or model), a `mode="precise"` request returns 503 with a zh-Hant message listing what is missing, e.g. 「精準模式缺少：qwen-image-edit-2511-Q4_K_M.gguf」.
- Both modes keep `kind = pose`. Mode is told apart by `workflow_name`:
  - `qwen_edit_pose_cutout` = 精準;
  - `sdxl_pose_cutout` = 快速.
  `GenerationOut` gains `pose_mode: "precise" | "fast" | null`, computed from that.
- The frontend needs to know whether precise mode is available. Add `pose_modes: {precise: bool, fast: bool}` to an existing endpoint, either `GET /system/preflight` or the response the editor already loads; builder's call. The rule is: no extra slow ComfyUI query when the editor opens. Reuse the preflight results cached at startup.
- No database migration. `workflow_name` already exists. `pose_mode` is a derived field.

## 7. Calibration (first step, before the code)

Use the GPU after checking with `nvidia-smi` that it is idle. Use the same reference rabbit, 6 actions and 2 seeds as the comparison experiment. Write results to `experiments/results/phase6_qwen_pose_calibration_<ts>/`.
- Add to the prompt: no ground shadow, the scarf only around the neck, white furry arms (to stop red sleeves).
- Measure with the largest-component mask post-processing applied.
- Pass criteria:
  - consistency average ≥ 4.0, action ≥ 4.5;
  - all 12 masks clean after the largest-component step;
  - red sleeves in ≤ 3 of 12 images;
  - shadows that end up in the line art ≤ 2 of 12.
- About seed variation: in the experiment the two seeds gave nearly identical images. In calibration, try adding a small random variation to the prompt (e.g. facing slightly left/right, different expression) and see whether that brings back the variety needed for 「再產生一張」 without hurting consistency. If it doesn't work, accept less variety and say so in the notes.
- Use the best prompt as the default, written into `prompts.py`.

## 8. Success criteria

- Real-GPU acceptance (same approach as Phase 5 Task 14): one page each for the three scenes, using precise mode, all reach "pass". Additionally, on the same page generate one fast and one precise image, switch between them, and check the badges and the compose flow are correct.
- No regression in fast mode (existing tests all pass).
- Precise mode taking about 25–30 s per image is acceptable. Switching from background (SDXL) to pose (Qwen) reloads the model, so the first image may take up to about 40 s; the UI's 「約 30 秒」 hint is a rough guide.
- All modules have tests. ComfyUI tests are mocked:
  - the manifest maps to nodes;
  - the reference image is passed to `CHARACTER_REF`;
  - mode routing;
  - 503 message;
  - largest-component mask;
  - `pose_mode` derivation;
  - frontend switch and memory, falling back when unavailable, badges.

## 9. Left to the builder

- Mask post-processing: whether it lives in the worker or the compose service, and which algorithm (e.g. scipy/PIL connected components). Must be deterministic and tested.
- Where `pose_modes` is exposed (§6).
- Whether to include the character description in the Qwen prompt (decided by calibration).
- Styling of the switch and badges, following the existing tokens and segmented-control style.

## 10. Calls made by the author (please check)

1. **Who scores calibration, and what if it fails**: the person running it (the controller) views each image, as in the comparison experiment. If it fails, iterate on the prompt up to 3 rounds. If it still fails but precise mode is still clearly better than fast on both consistency and action, keep precise as the default and record the shortfall in NOTES and the end-of-phase report. Only the default prompt depends on calibration; the UI and API work can proceed in parallel.
2. **Shadow**: handled by the prompt (no ground shadow) plus the largest-component step. If calibration shows a shadow connected to the feet entering the line art in more than 2 of 12 images, add a mask trimming step (e.g. remove a thin horizontal strip below the feet) with tests. Only newly stored masks are processed; existing Phase 5 generations are not reprocessed.
3. **Freshness of availability**: the preflight result is cached in the backend at startup and refreshed every time `GET /system/preflight` runs. `POST /poses` trusts the cache and does not query ComfyUI live. If ComfyUI was unreachable at startup (status unknown), precise counts as available. A missing node or model when the generation actually runs goes through the existing failure path (the generation becomes FAILED and the error message shows on screen), so nothing fails silently.
4. **Regenerating uses the switch's current mode**: there is no "regenerate from a thumbnail" feature, so a thumbnail's badge only describes how it was made.
5. **The API defaults to `precise`**: the frontend always sends `mode` explicitly. Existing tests that omit `mode` either get updated or set the preflight cache to "precise available" in mocks. There is no silent server-side fallback to fast: if the cache says it is unavailable, return 503 (whether or not `mode` was sent).
6. **Prompt variation is saved**: if calibration adopts random variation, the full prompt including the variation goes into the generation's `prompt` field, and the chosen variation also goes into `params_json`, as CLAUDE.md requires.
