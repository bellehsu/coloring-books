# Background style switch (簡潔 Qwen / 細緻 SDXL) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pick the background style 「簡潔」 (Qwen-Image-Edit-2511, new `qwen_background` workflow) or 「細緻」 (existing `sdxl_background`). Default is 簡潔. A page with a usable sketch always uses 細緻.

**Architecture:**
- The backend decides the effective style at enqueue time with one pure function and stores it in request_json. The engine routes on that value; the queue family follows it.
- A new workflow folder, `GET /api/system/background-styles`, and an optional `{style}` body on the single and batch background endpoints.
- Frontend: a remembered Segmented switch on the 背景 tab, badges, and confirmation text.

**Tech Stack:** FastAPI, SQLModel, SQLite, ComfyUI 0.36.0 (+ComfyUI-GGUF), Next.js 16 + React 19 + TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-10-qwen-background-design.md`

## Global Constraints

- Style values: `"simple"` (簡潔, Qwen) and `"detailed"` (細緻, SDXL). API default `"simple"`; any other value → 422.
- request_json keys: `background_style` (effective) and `background_style_requested`. Old BACKGROUND rows without the key → `"detailed"`.
- Usable sketch = `sketch_path` set **and** `controlnet_strength > 0`.
- Qwen readiness comes only from the cached preflight (`app.state.preflight`). Never contact ComfyUI live.
- Qwen prompt, verbatim (QA1): `{scene}. Simple coloring book background, a few large simple objects, lots of empty space, children's picture book illustration, flat colors, thick clean black outlines, simple shapes. Empty scene, no people, no animals, no characters, no text.` Negative prompt is `""`.
- UI copy, verbatim:
  - group label 「背景風格」; options 「簡潔」 / 「細緻」;
  - hints 「約 20 秒，線條少、適合小小孩」 / 「約 10 秒，細節多，可以照構圖草圖」;
  - sketch note 「有構圖草圖時用細緻，才會照草圖構圖」;
  - confirmation 「會為 N 頁產生背景（簡潔）」, plus 「，有草圖的 K 頁用細緻」 when K > 0;
  - badges 「簡潔」 / 「細緻」.
- localStorage key `coloring.bgStyle`, default `simple`.
- Verify ComfyUI node classes and inputs against the running ComfyUI's `/object_info`. Never guess. Never download models.
- Never touch `storage/app.db`. Single worker. Commit locally only, with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`.
- Tests:
  - backend `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`;
  - frontend `cd frontend && npm test && npm run lint && npx tsc --noEmit`;
  - after any API change: `PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run gen:api` in `frontend/`;
  - e2e in the scratch copy `<scratchpad>/e2e-copy2/frontend` (rsync excluding node_modules/.next/storage/.git/.superpowers/accept*.mjs/shot.mjs/experiments; `npm run e2e` with `PYTHON=` set). The repo's own frontend/ is locked by the user's `next dev`.

## Review Focus

1. **A page with a sketch at strength > 0 while the user picked 簡潔** must produce an SDXL background that uses the sketch, through both the single and the batch endpoints. Tests: Task 2.
2. **Qwen not ready** (preflight says `qwen_background` is missing): 簡潔 requests still succeed as 細緻, with `background_style_requested="simple"` recorded. The UI disables 簡潔 and lists what is missing. Tests: Task 2, Task 4.
3. **Old clients and old rows:** a POST with no body must behave as `simple`; an old BACKGROUND row (no key) shows badge 細緻, is decoded as SDXL by the worker, and has family `sdxl`. Tests: Tasks 1 and 2.
4. **The switch must not overwrite the remembered choice** when it is forced to 細緻 by a sketch or by unavailability. Moving to a page without a sketch shows the remembered style again. Test: Task 4.
5. **Retry and regenerate** of a 簡潔 background: re-running an existing generation (the worker re-reads request_json) uses Qwen again, not SDXL. Test: Task 1 (decode round-trip).

---

### Task 1: qwen_background workflow, prompt, engine routing, family

**Files:**
- Create: `comfy_workflows/qwen_background/workflow_api.json`, `comfy_workflows/qwen_background/manifest.yaml`
- Modify:
  - `backend/app/services/prompts.py` (`qwen_background_prompt`, `QWEN_BACKGROUND_VARIATIONS`, `pick_background_variation`)
  - `backend/app/services/engine.py` (`GenerationRequest.background_style: str = "detailed"`)
  - `backend/app/services/worker.py` (`encode_request`/`decode_request`)
  - `backend/app/services/comfy_engine.py` (`BACKGROUND_WORKFLOWS`)
  - `backend/app/services/scheduling.py` (`family_of`)
- Test: `tests/test_workflow_registry.py`, `tests/test_prompts.py`, `tests/test_comfy_engine.py`, `tests/test_worker.py`, `tests/test_scheduling.py`

**Interfaces:**
- Produces:
  - `BACKGROUND_WORKFLOWS: dict[str, str] = {"simple": "qwen_background", "detailed": "sdxl_background"}` in comfy_engine (replaces the `BACKGROUND_WORKFLOW` constant's use for routing; keep the constant name only if other code imports it).
  - `qwen_background_prompt(scene: str, variation: str | None = None) -> str`. The variation is inserted after `{scene}.` as `{variation}. ` when given.
  - `QWEN_BACKGROUND_VARIATIONS: tuple[str, ...] = ()` (filled by Task 3).
  - `pick_background_variation(rng: random.Random) -> str | None` (None when the tuple is empty).
  - `encode_request(..., background_style: str | None = None, background_style_requested: str | None = None, variation=...)`. When `background_style` is given, it writes both style keys and `variation`.
  - `decode_request` sets `background_style=d.get("background_style") or "detailed"`.
  - `family_of`: BACKGROUND with `background_style == "simple"` → `"qwen"`.
- Workflow graph: build it exactly from the spike's `qwen_bg_graph` in `experiments/results/phase7_qwen_background_20261110_1104/run.py`, option a with no image1. Titles: UNET, TEXT_ENCODER, VAE, MODEL_SAMPLING, CFG_NORM, LIGHTNING_LORA, POSITIVE, NEGATIVE, LATENT, SAMPLER, DECODE, OUTPUT. Copy model filenames and fixed values from `comfy_workflows/qwen_edit_pose_cutout/`.
- Manifest parameters: `prompt` (string, required, max 2000 → POSITIVE.prompt), `seed`, `width`/`height` (512–1536, defaults 864/1216 → LATENT). Model list and required node classes follow the pose manifest's style.

- [ ] **Step 1: Write failing tests**
  - `test_qwen_background_manifest_loads`: the registry loads `qwen_background`; its params are exactly {prompt, seed, width, height}; it lists the 4 model files the pose workflow uses (unet gguf, qwen_2.5_vl text encoder, qwen_image_vae, Lightning LoRA). There is no LoadImage node and no BiRefNet node.
  - `test_qwen_background_prompt`: `qwen_background_prompt("in a park")` equals the verbatim QA1 string with `in a park`. With `variation="wide view"`, it contains `"in a park. wide view. Simple coloring book background"`.
  - `test_pick_background_variation_empty_returns_none`.
  - Engine, using the existing fake comfy in tests:
    - `test_background_simple_routes_to_qwen`: a BACKGROUND request with `background_style="simple"` builds `qwen_background` with values {prompt, seed, width, height}, uploads no structure image, and records `workflow_name="qwen_background"`.
    - `test_background_detailed_unchanged`: the current SDXL values, including the structure image.
  - Worker: `test_encode_decode_background_style_roundtrip` (simple + requested simple + variation), and `test_decode_old_background_row_is_detailed`.
  - Scheduling: `family_of(BACKGROUND, {"background_style":"simple"})` → qwen; with `"detailed"`, with no key, or with broken JSON → sdxl.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement. Before writing `workflow_api.json`, query `http://127.0.0.1:8188/object_info/<Class>` for each class used and check every input name. If ComfyUI is down, copy only node shapes already present in `qwen_edit_pose_cutout/workflow_api.json` (the same classes) and say so in the report.
- [ ] **Step 4:** Run the whole backend suite; it must pass.
- [ ] **Step 5:** Commit `feat(engine): qwen_background workflow and style routing`.

### Task 2: style decision, API body, provenance, background-styles endpoint

**Files:**
- Create: `backend/app/services/background_styles.py`
- Modify:
  - `backend/app/services/pose_modes.py` (generalise `availability` to accept a `{mode: workflow}` mapping; keep the existing call sites' behaviour)
  - `backend/app/api/compose.py` (`_enqueue_background`, single and batch endpoints)
  - `backend/app/api/schemas.py` (`BackgroundCreate`, `BackgroundStyle`, `GenerationOut.background_style`, `BackgroundStylesOut`)
  - `backend/app/main.py` (`GET /api/system/background-styles`)
- Regenerate: `frontend/src/api/openapi.json`, `schema.d.ts`
- Test: `tests/test_api_compose.py`, `tests/test_pose_modes.py`, new `tests/test_background_styles.py`

**Interfaces:**
- Consumes: Task 1's `encode_request(background_style=..., background_style_requested=..., variation=...)`, `qwen_background_prompt`, `pick_background_variation`.
- Produces:
  - `BackgroundStyle = Literal["simple", "detailed"]`.
  - `decide_style(requested: BackgroundStyle, *, has_usable_sketch: bool, simple_available: bool) -> BackgroundStyle`, a pure function in `background_styles.py`.
  - `style_availability(report: PreflightReport | None) -> dict[str, ModeStatus]` for `{"simple": "qwen_background", "detailed": "sdxl_background"}`, using the same rules as pose modes.
  - `POST /api/pages/{id}/backgrounds` and `POST /api/books/{id}/backgrounds/batch` accept the optional JSON body `BackgroundCreate {style: BackgroundStyle = "simple"}`. A missing body is the same as the default.
  - `GenerationOut.background_style: BackgroundStyle | None`: the effective style for kind background (`"detailed"` for old rows), None for other kinds.
  - `GET /api/system/background-styles` → `{simple: {available, missing}, detailed: {available, missing}}`.
- For an effective `simple`: prompt = `qwen_background_prompt(scene, variation)`, negative_prompt = `""`, and the sketch snapshot is **still stored** if a sketch exists at strength 0 (provenance), but the engine ignores it. For `detailed`: today's prompt, negative and structure handling.

- [ ] **Step 1: Write failing tests**
  - `test_decide_style` table:
    - (simple, sketch, avail) → detailed;
    - (simple, no sketch, avail) → simple;
    - (simple, no sketch, not avail) → detailed;
    - (detailed, no sketch, avail) → detailed.
  - API tests:
    - `test_background_default_style_simple`: no body → request_json has `background_style="simple"` and `background_style_requested="simple"`, the prompt starts with the scene followed by `". "`, negative is `""`, and the response has `background_style == "simple"`.
    - `test_background_sketch_forces_detailed`: sketch with strength 0.4 and `{style:"simple"}` → `"detailed"`, `structure_image` snapshot set, SDXL prompt.
    - `test_background_sketch_strength_zero_uses_simple`.
    - `test_background_qwen_unavailable_falls_back`: set `app.state.preflight` to a report where `qwen_background` is not ok → `"detailed"`, requested `"simple"`, status 202.
    - `test_background_invalid_style_422`.
    - `test_batch_style_applies_per_page`: 3 pages, one with a sketch at strength 0.4 and body `{style:"simple"}` → two simple and one detailed. With an empty body → simple.
    - `test_generation_out_background_style_old_row_detailed`.
    - `test_background_styles_endpoint` (unavailable lists the missing filenames; with no report, both are available).
  - Pose-modes tests stay green, unchanged.
- [ ] **Step 2:** Run and confirm they fail.
- [ ] **Step 3:** Implement, then run `PYTHON=... npm run gen:api` in `frontend/`.
- [ ] **Step 4:** Backend suite passes, and `npx tsc --noEmit` passes.
- [ ] **Step 5:** Commit `feat(api): background style (simple/detailed) with sketch and availability fallback` (generated types in the same commit).

### Task 3: variation calibration on real GPU (the controller runs this)

- [ ] Check that `nvidia-smi` is idle and the ComfyUI `/queue` is empty. Tell the user before starting, and unload models afterwards (`POST /free`).
- [ ] Run the real `qwen_background` workflow through `WorkflowRegistry.build_prompt` on the spike's 6 scenes × seeds 101/202, for QA1 without variation and for 2–3 candidate variation lists (viewpoint/framing phrases, e.g. "wide view from the front", "seen from a little to the left", "close view, objects bigger"). Same metrics as the spike: `render_base(img, (2244, 3272), STANDARD, 11)` → `qa.evaluate`, plus counts of people/animals, a contact sheet and a judgement by eye.
- [ ] Pass criteria (spec §3.5): mean gaps ≤ 11.5, mean tiny regions ≤ 99, 0 people/animals, and the same scene clearly different across two generations. Put the passing list into `QWEN_BACKGROUND_VARIATIONS`, with a test asserting its length and that every entry is non-empty. If none pass, leave it empty and record why.
- [ ] Record the run in `experiments/results/phase8_qwen_background_variation_<ts>/` (NOTES.md, run.py, results.csv) and commit.

### Task 4: frontend switch, badges, confirmation, e2e, docs

**Files:**
- Create: `frontend/src/lib/bgStyle.ts` (+ test)
- Modify:
  - `frontend/src/api/hooks.ts` (`useBackgroundStyles`; the background create and batch mutations send `{style}`)
  - `frontend/src/features/editor/BackgroundPanel.tsx` (+ css, test)
  - `frontend/src/features/editor/EditorPage.tsx` (+ test)
  - `frontend/src/features/editor/HistoryTray.tsx` (+ test)
  - `frontend/src/features/editor/batchBackgrounds.ts` (+ test)
  - `frontend/src/features/editor/Filmstrip.tsx` (confirmation text only)
  - `frontend/e2e/` (new or extended spec; fake backend)
  - `README.md`, `CLAUDE.md` (生成路線: one line)

**Interfaces:**
- Consumes: Task 2's API (`style` body, `background_style` on generations, `/api/system/background-styles`).
- Produces:
  - `type BgStyle = "simple" | "detailed"`;
  - `readBgStyle()`, `writeBgStyle(s)`;
  - `effectiveBgStyle(stored: BgStyle, opts: { hasSketch: boolean; simpleAvailable: boolean | undefined }) -> BgStyle` (a sketch or a known-unavailable 簡潔 → "detailed"; otherwise stored);
  - `batchConfirmText(plan, style: BgStyle, sketchCount: number)`.
- A page has a usable sketch when `page.sketch_path` is set and the current strength value is > 0. Use the panel's live slider value so it updates while dragging.

- [ ] **Step 1: Write failing tests**
  - `bgStyle.test.ts`:
    - default simple;
    - read/write round-trip;
    - a bad stored value gives simple;
    - localStorage throwing gives simple;
    - `effectiveBgStyle` table.
  - `BackgroundPanel.test.tsx`:
    - the radiogroup 「背景風格」 exists with 「簡潔」 checked by default and the hint 「約 20 秒，線條少、適合小小孩」;
    - choosing 細緻 shows 「約 10 秒，細節多，可以照構圖草圖」 and stores `detailed`;
    - on a page with a sketch at strength 0.4: 簡潔 is disabled, 細緻 is checked, the note 「有構圖草圖時用細緻，才會照草圖構圖」 is shown, and the stored value stays `simple`;
    - simple unavailable: disabled, with the missing list shown;
    - 產生背景 posts `{style:"simple"}`, or `{style:"detailed"}` when forced.
  - `HistoryTray.test.tsx`: background cards show 「簡潔」 / 「細緻」 badges from `background_style`.
  - `batchBackgrounds.test.ts`: `batchConfirmText(plan, "simple", 0)` → 「會為 3 頁產生背景（簡潔）」; with K = 1 it adds 「，有草圖的 1 頁用細緻」 inside the parentheses, before the skip list. For detailed, the text is 「會為 3 頁產生背景（細緻）」 with no sketch clause.
  - `EditorPage.test.tsx`: the batch POST body carries the stored style.
- [ ] **Step 2:** Run and confirm they fail.
- [ ] **Step 3:** Implement using the existing `Segmented` and badge styles (mirror PosePanel and HistoryTray's pose badge). Use design tokens only.
- [ ] **Step 4:** e2e (fake backend):
  - the default is 簡潔;
  - generate one background → its card shows 「簡潔」;
  - switch to 細緻 and generate → 「細緻」;
  - reload → 細緻 is still selected;
  - a seeded page with a sketch shows 簡潔 disabled.
  All e2e specs pass in the scratch copy.
- [ ] **Step 5:** README (the background section, the API table for the body and background-styles, the e2e list) and the CLAUDE.md 生成路線 line: 「背景風格（Phase 8，2026-11-10）：預設「簡潔」`qwen_background`（Qwen-Image-Edit-2511 無參考圖），「細緻」為 `sdxl_background`；有構圖草圖（強度>0）時一律細緻。」.
- [ ] **Step 6:** Commit `feat(editor): background style switch` and `docs: background styles`.

### Task 5: real-GPU acceptance (the controller runs this)

- [ ] Check the GPU is idle and tell the user first. Use the same setup as Phase 7 (backend 8020 with scratch storage, frontend 3020, `accept7.mjs` adapted).
  - Run 1: 3 pages with no sketch, 產生所有背景 (簡潔), then precise poses → compose → line art → QA → pass. Compare against Phase 7's 166 s, and the gaps and tiny regions per page.
  - Run 2: add a 4th page with an uploaded sketch → its background is SDXL (badge 細緻), and the composition follows the sketch.
  - Unload models afterwards.
- [ ] Record in `experiments/results/phase8_qwen_background_ui_check_<ts>/NOTES.md` and commit.

---

## Self-Review record

- Spec coverage:
  - §3.1 → Task 2 (Task 1 for encode/decode);
  - §3.2–3.4 → Task 1;
  - §3.5 → Task 3;
  - §3.6 → Task 4;
  - §3.7 → Task 4 (calibration docs in Task 3);
  - §4 → Tasks 1, 2, 4 and 5.
- Names are consistent across tasks: `background_style`, `background_style_requested`, `BACKGROUND_WORKFLOWS`, `qwen_background_prompt`, `QWEN_BACKGROUND_VARIATIONS`, `pick_background_variation`, `decide_style`, `style_availability`, `BgStyle`, `effectiveBgStyle`, `batchConfirmText(plan, style, sketchCount)`.
