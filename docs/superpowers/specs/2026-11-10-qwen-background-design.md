# Background style switch 「簡潔」(Qwen) / 「細緻」(SDXL): design

Date: 2026-11-10. Builds on Phase 7 (model-grouped queue plus generate all backgrounds) and the spike in
`experiments/results/phase7_qwen_background_20261110_1104/` (commit 35ce5d7).

## 1. Why

The spike compared the app's own SDXL background with Qwen-Image-Edit-2511 used as plain text-to-image (no input image):

| | gaps | tiny regions | visual (1–5) | time per image, warm |
|---|---|---|---|---|
| SDXL `sdxl_background` (current) | 52.9 | 207.9 | 1.9 | 6 s |
| Qwen, no image1 ("QA1") | 9.5 | 82.3 | 4.3 | 12.5–18.5 s |

Qwen backgrounds are much cleaner and better suited to children colouring in: big simple shapes, few tiny regions. When backgrounds and precise poses are both Qwen, model swaps also go down (spike: about 57 s → 45 s per page in the interactive flow).

User decisions (2026-11-10):
- Add a switch on the 背景 tab with two options, **「簡潔」** (Qwen) and **「細緻」** (SDXL).
- It **defaults to 簡潔** and the browser **remembers the last choice**, like the pose 精準/快速 switch.
- **A page with a sketch automatically uses 細緻**, with a note explaining why: Qwen cannot follow a sketch.
- History cards carry a 簡潔/細緻 badge.

## 2. Goals, non-goals, anti-goals

**Goals**
- The background style can be switched. 簡潔 uses the new `qwen_background` workflow; 細緻 keeps `sdxl_background` exactly as it is today.
- Single 「產生背景」 and 「產生所有背景」 follow the same rules.
- The queue groups Qwen backgrounds with precise poses (same model family).
- Every generation records which style and workflow it used.

**Non-goals**
- Qwen will not follow sketches (that would need another experiment).
- Old generations, the old whole-page flow and pose modes are unchanged.
- No new models: every Qwen file is already installed for precise poses.

**Anti-goals**
- A page with a sketch must never silently ignore it.
- If the Qwen files are missing, backgrounds must not break. 簡潔 is unavailable, and 細緻 is used automatically.
- 「再產生一張」 must not become useless (§3.5).

## 3. Behaviour

### 3.1 API and which model runs
- `POST /pages/{id}/backgrounds` gains an optional body `{style: "simple" | "detailed"}`, default `"simple"`. Any other value returns 422.
- `POST /books/{id}/backgrounds/batch` gains the same optional body, applied to every page. An empty body (Phase 7 clients) means `"simple"`.
- The effective style for a page is decided at enqueue time:
  1. The page has a usable sketch (`sketch_path` set and `controlnet_strength > 0`) → `detailed`, whatever was requested. A sketch with strength 0 is ignored by SDXL anyway, so the requested style applies.
  2. `simple` was requested but `qwen_background` is not ready according to the cached preflight (`app.state.preflight`, the same source as pose-modes) → `detailed`.
  3. Otherwise the requested style.
- request_json records `background_style` (effective) and `background_style_requested`. Old rows have no key and count as `detailed`. The engine routes on `background_style` and never decides again.
- `GenerationOut` exposes `background_style` (null for non-BACKGROUND kinds; `detailed` for old background rows).
- `GET /api/system/background-styles` → `{simple: {available, missing}, detailed: {available, missing}}`. Built like `/api/system/pose-modes`, and refreshed by `GET /api/system/preflight`.
- A sketch file that is missing keeps today's behaviour: single → 409 「草圖檔案遺失，請重新上傳」; batch → skip with 「草圖檔案遺失」. This is checked before the style is decided.
- Fallback to `detailed` happens only at enqueue time. A Qwen job that fails while running fails like any other job: the error is recorded and the user can retry. It is not re-run on SDXL.

### 3.2 `qwen_background` workflow
- New folder `comfy_workflows/qwen_background/` (workflow_api.json + manifest.yaml).
- It is `qwen_edit_pose_cutout` without CHARACTER_REF and the BiRefNet nodes, exactly the spike's `qwen_bg_graph` (table in the spike's NOTES):
  - UnetLoaderGGUF → ModelSamplingAuraFlow 3.1 → CFGNorm → Lightning LoRA;
  - `TextEncodeQwenImageEditPlus` with **no image1**, for POSITIVE and for NEGATIVE (empty);
  - EmptySD3LatentImage;
  - KSampler with 4 steps, cfg 1, euler/simple;
  - VAEDecode → SaveImage.
- Manifest parameters: `prompt`, `seed`, `width`, `height`, with the same ranges as `sdxl_background`. Check node classes and inputs against `/object_info` again.
- Size: the book's `generation_size`.

### 3.3 Prompt
`prompts.qwen_background_prompt(scene, variation=None)` uses the spike's QA1 wording:
`{scene}. Simple coloring book background, a few large simple objects, lots of empty space, children's picture book illustration, flat colors, thick clean black outlines, simple shapes. Empty scene, no people, no animals, no characters, no text.`
There is no negative prompt (cfg 1 ignores it), so `negative_prompt` is stored as "".

### 3.4 Queue family
`family_of`: a BACKGROUND whose `background_style == "simple"` → `qwen`. Nothing else changes.

### 3.5 Variety for 「再產生一張」
In the spike, two seeds gave almost the same layout. Calibrate a small list of composition variations (viewpoint or framing phrases), add one to the prompt at random per generation (never the phrase of the page's most recent simple background, so 「再產生一張」 always changes the composition; added after the final review), and record it in request_json as `variation`. Adopt the list only if a GPU calibration (6 scenes × 2 seeds) meets all of these:
- line-art metrics within 20% of QA1 (gaps ≤ about 11.5, tiny regions ≤ about 99);
- no people or animals;
- two generations of the same scene look clearly different.
If no variation passes, ship without one and say so in the phase report.

### 3.6 Frontend
- The 背景 tab gets a Segmented radiogroup 「背景風格」 with 「簡潔」 and 「細緻」, styled like PosePanel's 「產生方式」.
  - Hints: 簡潔 「約 20 秒，線條少、適合小小孩」; 細緻 「約 10 秒，細節多，可以照構圖草圖」.
  - It replaces the current fixed 「約 10 秒」 hint line; the queue count text stays.
- localStorage key `coloring.bgStyle`, default `simple`, with the same read/write/effective helpers as `poseMode.ts`.
- On a page with a usable sketch (strength > 0), 簡潔 is disabled and 細緻 is shown as selected, with the note 「有構圖草圖時用細緻，才會照草圖構圖」. The stored choice is not changed.
- When 簡潔 is unavailable (background-styles), it is disabled and shows the missing list; 細緻 is used.
- The 「產生所有背景」 confirmation shows the style, e.g. 「會為 3 頁產生背景（簡潔）」. When some pages have sketches it adds 「，有草圖的 N 頁用細緻」. Batch requests send the effective stored style.
- History cards show a 「簡潔」 or 「細緻」 badge, like the pose badges.

### 3.7 Docs
- CLAUDE.md 生成路線 gets one line.
- README background section and API table are updated.
- The calibration is recorded in `experiments/results/`.

## 4. Success criteria
- Unit tests, with ComfyUI mocked:
  - the style decision for each case: none, sketch with strength > 0, sketch with strength 0, Qwen not ready, missing sketch file;
  - engine routing and parameters;
  - request_json provenance;
  - `family_of`;
  - batch body and per-page decision;
  - background-styles endpoint;
  - frontend switch, disabled states, remembered choice, badges and confirmation text.
- e2e (fake backend): switch, badge and a batch with one sketch page.
- Real GPU: the Phase 7 acceptance flow (3 pages with no sketches, 產生所有背景 with 簡潔, then precise poses), with times compared against the Phase 7 run (166 s); plus one page with a sketch that stays 細緻.

## 5. Calls made by the author (not decided by the user)
- A sketch with strength 0 counts as "no sketch".
- When Qwen isn't ready, requests quietly use 細緻 and record it (the badge shows 細緻), with no 503. The UI disables 簡潔 and lists what's missing.
- The style is a request parameter, not a book setting, so mixed books are allowed.
- The variation is calibrated during this phase against explicit pass criteria.
- Exact hint wording above is the author's and can be adjusted in review.
