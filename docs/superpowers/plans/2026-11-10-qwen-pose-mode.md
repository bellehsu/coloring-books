# Character pose precise/fast modes: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 「精準（Qwen-Image-Edit-2511）｜快速（SDXL）」 switch to the character pose step, defaulting to precise. Compose, line art, QA and export stay unchanged.

**Architecture:**
- New workflow folder `comfy_workflows/qwen_edit_pose_cutout/` (Qwen GGUF + Lightning + BiRefNet background removal).
- `GenerationRequest.pose_mode` decides which workflow `ComfyEngine` uses for a POSE.
- The API takes `mode`, the backend caches the preflight result to judge availability (503), and the worker keeps only the largest connected component of the pose mask.
- The frontend switch is remembered in localStorage, and the history thumbnails show a mode badge.

**Tech Stack:** FastAPI, SQLModel, ComfyUI 0.36 (ComfyUI-GGUF), OpenCV/numpy, Next.js 16 + React 19 + TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-10-qwen-pose-mode-design.md` (binding; §10 holds the author's calls).

## Global Constraints

- Mode values: `"precise" | "fast"`. API default `"precise"`; anything else → 422.
- Workflow names: precise = `qwen_edit_pose_cutout`, fast = `sdxl_pose_cutout` (unchanged).
- UI copy (zh-Hant, verbatim):
  - switch labels: 「精準」「快速」;
  - hints: 精準 「約 30 秒，角色最像、動作最準」, 快速 「約 10 秒，可能和角色有落差」;
  - badges: 「精準」「快速」.
- localStorage key: `coloring.poseMode`. Wrap reads and writes in try/catch; if nothing is stored or the value is invalid, use `"precise"`.
- Precise unavailable → 503, message starting with 「精準模式缺少：」 followed by the missing items separated by 「、」.
- No DB migration. The mode lives in the generation's `request_json` (`pose_mode`). A request_json without the key (Phase 5 rows) counts as `"fast"`.
- Precise workflow models (no downloads; already on this machine):
  - `unet/qwen-image-edit-2511-Q4_K_M.gguf` (`UnetLoaderGGUF`)
  - `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` (`CLIPLoader` type `qwen_image`)
  - `vae/qwen_image_vae.safetensors`
  - `loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors`
  - `background_removal/birefnet.safetensors`
  - Fixed values: steps 4, cfg 1, euler/simple, shift 3.1, CFGNorm 1.0, LoRA 1.0, 832×1216.
- Verify node names and inputs against the running ComfyUI's `/object_info`; never guess. Tests must run with ComfyUI mocked.
- Never touch `storage/app.db`. Use the GPU only after `nvidia-smi` shows it idle. Commit locally only. Commit trailer:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`.
- Python tests: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`. Frontend: `cd frontend && npm test && npm run lint && npx tsc --noEmit`.
  e2e: the user's `next dev` holds the lock, so rsync into the scratch copy and run it there (see Task 6).

## Review Focus

1. **Precise unavailable** (no ComfyUI-GGUF or missing GGUF): the switch shows 精準 disabled and falls back to 快速; a direct API call returns a 503 listing what is missing; the service does not crash. Tests: Task 4 and Task 5.
2. **Old pose generations** (Phase 5, no `pose_mode` in request_json) must count as 快速, and the badge must not be blank or wrong. Test: Task 4.
3. **Mask with a detached shadow or several blobs**: keep only the largest piece; the cutout and composite contain no shadow piece. An all-black mask stays all black (the cutout then reports 409 as before). Test: Task 2.
4. **localStorage throws or holds an old value** (private window, a value like `"qwen"`): the UI falls back to 精準 and does not crash. Test: Task 5.
5. **A queued or running pose generation also gets the right badge** (it has no `workflow_name` yet), so `pose_mode` must come from request_json. Test: Task 4.

---

### Task 1: precise workflow folder

**Files:**
- Create: `comfy_workflows/qwen_edit_pose_cutout/workflow_api.json`, `comfy_workflows/qwen_edit_pose_cutout/manifest.yaml`
- Test: `tests/test_workflow_registry.py` (append)

**Interfaces:**
- Produces: workflow `qwen_edit_pose_cutout`, whose logical parameters are `prompt`, `seed`, `width`, `height`, `character_image` (and optionally `lora_strength`, `steps`, `cfg`, with defaults equal to the Global Constraints values). Outputs, in order: title `OUTPUT` and title `MASK`.
- Node titles: UNET, TEXT_ENCODER, VAE, LIGHTNING_LORA, CHARACTER_REF, POSITIVE, NEGATIVE, LATENT, SAMPLER, OUTPUT, BG_REMOVAL_MODEL, MASK.

- [ ] **Step 1: Write the failing tests.** `test_qwen_pose_manifest_loads_and_maps_titles`: after `WorkflowRegistry(...).load()`, `get("qwen_edit_pose_cutout").build_prompt({prompt:"p", seed:1, width:832, height:1216, character_image:"c.png"})` succeeds, and in the built prompt:
  - the node titled CHARACTER_REF has `image == "c.png"`;
  - POSITIVE has `prompt == "p"`;
  - SAMPLER has `seed == 1`, `steps == 4`, `cfg == 1`;
  - LATENT is 832×1216;
  - the model list contains the 5 model filenames from Global Constraints;
  - outputs are `["OUTPUT","MASK"]`.

  `test_qwen_pose_wiring`: SAMPLER.model traces back to LIGHTNING_LORA (through CFGNorm/ModelSamplingAuraFlow) and LIGHTNING_LORA traces back to UNET; the POSITIVE/NEGATIVE image input traces back to CHARACTER_REF; the RemoveBackground image input comes from VAEDecode. This follows the existing wiring tests for `sdxl_pose_cutout`.
- [ ] **Step 2:** Run, and confirm it fails because the workflow does not exist.
- [ ] **Step 3: Build the workflow.**
  - Start from `wf_qwen()` + `bg_nodes()` in `experiments/results/phase5_pose_consistency_20261110_0739/run.py`, add `_meta.title` to every node, and write the manifest following the structure of `sdxl_pose_cutout/manifest.yaml`. Models section: one entry per model (filename, folder, source HF page, loader title/input). `custom_nodes` lists ComfyUI-GGUF (provides `UnetLoaderGGUF`).
  - Query `curl -s 127.0.0.1:8188/object_info/<Class>` for every class_type and input name to verify them. NEGATIVE prompt is fixed as the empty string and is not a parameter.
- [ ] **Step 4:** Tests pass. Then run `env -u PYTHONPATH .../python scripts/preflight.py` (needs ComfyUI 8188 up) and confirm `qwen_edit_pose_cutout` ok=true.
- [ ] **Step 5: Commit** `feat(workflows): qwen edit pose-cutout workflow`.

### Task 2: keep only the mask's largest connected component

**Files:**
- Modify: `backend/app/services/compose.py` (new function), `backend/app/services/worker.py` (call it when storing a POSE mask)
- Test: `tests/test_compose.py`, `tests/test_worker.py` (append)

**Interfaces:**
- Produces: `largest_component(mask_png: bytes) -> bytes`. It returns an L-mode PNG of the same size: only the largest 8-connected component of pixels >128 is kept as 255, everything else is 0. An all-black mask comes back all black.

- [ ] **Step 1: Write the failing tests.**
  - `test_largest_component_drops_detached_blob`: 100×100, a 40×40 body plus a detached 30×5 shadow → only the body's pixels remain, size unchanged.
  - `test_largest_component_keeps_all_black`.
  - `test_largest_component_soft_edges`: pixels at 200 on the body's edge become 255, and pixels at 50 become 0.
  - `test_worker_stores_largest_component_mask_for_pose`: FakeEngine returns a mask with two blobs → the stored `_mask.png` has only one.
  - `test_worker_mask_unchanged_for_non_pose`, or confirm that only POSE gets a mask: other kinds have no mask, so it is enough to state that it applies only to POSE.
- [ ] **Step 2:** Run, and confirm it fails.
- [ ] **Step 3: Implement it.** Use `cv2.connectedComponentsWithStats(..., connectivity=8)` and take the label with the largest area, excluding background. In the worker's step that writes the mask (`worker.py`, around lines 210–216), call it when `gen.kind == GenKind.POSE`.
- [ ] **Step 4:** Tests pass, and the whole backend suite passes.
- [ ] **Step 5: Commit** `feat(compose): keep the largest mask component for poses`.

### Task 3: calibrate the precise prompt (controller does this, GPU)

**Files:**
- Create: `experiments/results/phase6_qwen_pose_calibration_<YYYYMMDD_HHMM>/{NOTES.md,run.py,results.csv}`
- Modify: `backend/app/services/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Produces:
  - `QWEN_POSE_TEMPLATE: str` (contains `{action}`);
  - `QWEN_POSE_VARIATIONS: tuple[str, ...]` (may be empty);
  - `qwen_pose_prompt(action: str, variation: str | None = None) -> str`: with `variation` None or empty, it returns the template with only the action filled in; otherwise it inserts the variation phrase after the action, joined with 「, 」.
  - `pick_variation(rng: random.Random) -> str | None`: None when the tuple is empty.
- [ ] **Step 1:** Using the Task 1 workflow and Task 2's `largest_component`, run the same reference rabbit with 6 actions × 2 seeds. Compare 2–3 prompt versions (adding no ground shadow / scarf only around the neck / white furry arms) and with or without variation phrases. Score them visually against spec §7 and choose a winner. If it fails, iterate up to 3 rounds (spec §10.1).
- [ ] **Step 2:** Write `test_qwen_pose_prompt`:
  - the result contains the action;
  - it starts with `Make this exact character`;
  - it contains every phrase calibration decided on (e.g. `no ground shadow`);
  - with a variation, that phrase follows the action;
  - for an empty tuple, `pick_variation` returns None.
- [ ] **Step 3:** Implement it in `prompts.py`, with a comment pointing to the calibration folder.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5: Commit** `exp: qwen pose prompt calibration` + `feat(prompts): qwen pose prompt`.

### Task 4: backend modes, routing, availability, `pose_mode`

**Files:**
- Modify:
  - `backend/app/services/engine.py` (`GenerationRequest.pose_mode: str = "fast"`)
  - `backend/app/services/worker.py` (request_json → `pose_mode`)
  - `backend/app/services/comfy_engine.py` (`POSE_WORKFLOWS` routing and Qwen parameters)
  - `backend/app/api/schemas.py` (`PoseCreate.mode`, `GenerationOut.pose_mode`)
  - `backend/app/api/compose.py` (`create_pose`)
  - `backend/app/main.py` (preflight cache, `/api/system/pose-modes`)
  - `scripts/dev_fake_backend.py` (DemoEngine accepts both modes)
  - regenerated `frontend/src/api/openapi.json` and `schema.d.ts`
- Create: `backend/app/services/pose_modes.py`
- Test: `tests/test_comfy_engine.py`, `tests/test_compose_api.py` (or whichever file holds the existing compose API tests), `tests/test_pose_modes.py`, `tests/test_scripts.py`

**Interfaces:**
- Consumes: Task 1 workflow names, Task 3 `qwen_pose_prompt` and `pick_variation`.
- Produces:
  - `POSE_WORKFLOWS: dict[str, str] = {"precise": "qwen_edit_pose_cutout", "fast": "sdxl_pose_cutout"}` (in `comfy_engine.py`).
  - `pose_modes.availability(report: PreflightReport | None) -> dict[str, ModeStatus]`, where `ModeStatus = {available: bool, missing: list[str]}`:
    - if report is None or ComfyUI is unreachable, both are available (spec §10.3);
    - otherwise, for each mode, if its workflow report is ok=false then available=false, and `missing` lists the missing model filenames, node class_types and load errors.
  - `app.state.preflight: PreflightReport | None`: written at startup and on every `GET /api/system/preflight`.
  - `GET /api/system/pose-modes` → `{"precise": {"available": bool, "missing": [...]}, "fast": {...}}`. It reads the cache only and never contacts ComfyUI.
  - `PoseCreate.mode: Literal["precise","fast"] = "precise"`.
  - `GenerationOut.pose_mode: Literal["precise","fast"] | None`: `None` for non-POSE kinds; for POSE, the `pose_mode` in request_json, or `"fast"` when absent.
  - POSE generations of either mode keep `kind=pose`. For precise, `prompt = qwen_pose_prompt(action, variation)`, `negative_prompt = ""`, and `request_json` contains `pose_mode` and `variation`.
  - Fast keeps the existing `pose_prompt`/`POSE_NEGATIVE`. `user_text` is the action in both modes.

- [ ] **Step 1: Write the failing tests.**
  - Engine (mocked ComfyClient):
    - `precise` uses `qwen_edit_pose_cutout`, the reference image is uploaded and given to `character_image`, and no `ipadapter_*` is passed;
    - `fast` behaves as before;
    - a missing `pose_mode` means fast.
  - API:
    - `POST /poses {action}` with no mode → the generation's request_json has `pose_mode="precise"`;
    - `mode:"fast"` → `"fast"`;
    - `mode:"qwen"` → 422;
    - set `app.state.preflight` to a report where the qwen workflow is missing the GGUF → `mode:"precise"` returns 503 and detail starts with 「精準模式缺少：」 and contains `qwen-image-edit-2511-Q4_K_M.gguf`, while `mode:"fast"` still returns 202.
  - `pose_modes`:
    - None → both available;
    - unreachable → both available;
    - qwen missing a node → precise unavailable and missing contains the class_type.
  - `/api/system/pose-modes` returns the cached result, and `GET /api/system/preflight` updates the cache. Monkeypatch `run_preflight`.
  - `GenerationOut.pose_mode`:
    - an old POSE whose request_json has no key → `"fast"`;
    - a queued precise POSE → `"precise"`;
    - BACKGROUND → None.
  - Precise POSE `params_json`/`prompt` contains the variation when one was chosen. Monkeypatch `pick_variation` to return a fixed phrase.
  - DemoEngine: a POSE in either mode still returns a same-size mask.
- [ ] **Step 2:** Run, and confirm they fail.
- [ ] **Step 3: Implement** following the Interfaces above. Write the startup preflight into `app.state.preflight`. A preflight failure must never block the service.
- [ ] **Step 4:** Tests pass, and the whole backend suite passes. Run `cd frontend && PYTHON=... npm run gen:api` to regenerate the types; `npx tsc --noEmit` passes.
- [ ] **Step 5: Commit** `feat(api): precise/fast pose modes with availability` (types in the same commit).

### Task 5: frontend switch, memory, fallback, badges

**Files:**
- Modify: `frontend/src/api/hooks.ts` (`useGeneratePose` sends `mode`; new `usePoseModes()`), `frontend/src/features/editor/PosePanel.tsx` (+ module.css), `frontend/src/features/editor/HistoryTray.tsx` (+ module.css), `frontend/src/features/editor/EditorPage.tsx` (wiring)
- Create: `frontend/src/lib/poseMode.ts`
- Test: `frontend/src/lib/poseMode.test.ts`, `PosePanel.test.tsx`, `HistoryTray.test.tsx`, `EditorPage.test.tsx` (append)

**Interfaces:**
- Consumes: Task 4 `GET /system/pose-modes`, `PoseCreate.mode`, `GenerationOut.pose_mode`.
- Produces:
  - `type PoseMode = "precise" | "fast"`;
  - `readPoseMode(): PoseMode`;
  - `writePoseMode(m: PoseMode): void` (key `coloring.poseMode`, try/catch);
  - `effectiveMode(stored: PoseMode, avail: {precise: boolean; fast: boolean} | undefined): PoseMode`:
    - availability not loaded yet (undefined) → stored;
    - precise unavailable and fast available → `"fast"`;
    - otherwise stored.
  - `PosePanel` gains props `mode: PoseMode`, `onModeChange(m)`, `preciseUnavailableReason: string | null`. `onGenerate(action, mode)`.

- [ ] **Step 1: Write the failing tests.**
  - `poseMode`:
    - default `"precise"`;
    - after writing `"fast"`, reading returns `"fast"`;
    - a stored value of `"qwen"` → `"precise"`;
    - when `localStorage.getItem` throws → `"precise"`, and `writePoseMode` does not throw;
    - `effectiveMode` covers its three cases.
  - `PosePanel`:
    - the segmented switch (role radiogroup, accessible name 「產生方式」) shows 精準 selected by default, with the hint 「約 30 秒，角色最像、動作最準」;
    - clicking 快速 → `onModeChange("fast")` and the hint changes to 「約 10 秒，可能和角色有落差」;
    - generating calls `onGenerate(action, currentMode)`;
    - when `preciseUnavailableReason` is set, the 精準 radio is disabled and the reason text is shown.
  - `HistoryTray`: a POSE card with `pose_mode:"precise"` shows the badge 「精準」, `"fast"` shows 「快速」, and background cards show no badge.
  - `EditorPage`:
    - with a mocked `pose-modes` response where precise is unavailable, the pose tab shows 快速 selected and generating sends `mode:"fast"`;
    - switching to 快速 then remounting still shows 快速 (localStorage).
- [ ] **Step 2:** Run, and confirm they fail.
- [ ] **Step 3: Implement it.**
  - The switch style follows the existing segmented controls (e.g. the line-detail 簡單｜標準｜細緻 in LinePanel) and uses tokens only.
  - The badge sits in the card's top-left corner, so it doesn't overlap 「使用中」.
  - `usePoseModes` uses `staleTime: 60_000`.
- [ ] **Step 4:** `npm test && npm run lint && npx tsc --noEmit` all pass.
- [ ] **Step 5: Commit** `feat(editor): precise/fast pose mode switch and badges`.

### Task 6: e2e and docs

**Files:**
- Modify: `frontend/e2e/flow.spec.ts`, `README.md` (page flow, API table: `mode`, `/system/pose-modes`), `CLAUDE.md` (generation route section, one line: the pose step defaults to precise `qwen_edit_pose_cutout`, fast is `sdxl_pose_cutout`)
- Test: e2e

- [ ] **Step 1:** In `flow.spec.ts`'s main flow, after 「揮手」:
  - assert the switch defaults to 「精準」;
  - generate and check the request body has `mode:"precise"` and the history card badge is 「精準」;
  - switch to 「快速」 and generate another, which shows 「快速」;
  - pick one of them and continue the existing compose → line art → QA → export flow.

  Add a test where `page.route` makes `/api/system/pose-modes` return precise unavailable: 精準 is disabled and shows the reason, and the switch is on 快速.
- [ ] **Step 2: Run e2e** in the scratch copy:
  - `rsync -a --delete --exclude node_modules --exclude .next --exclude storage --exclude .git --exclude .superpowers /ai-data/repos/coloring-books/ <scratchpad>/e2e-copy/`
  - then in `e2e-copy/frontend`: `PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e`.
  - Everything passes, and running the flow spec with `--repeat-each 2` also passes.
- [ ] **Step 3:** Update the README and CLAUDE.md.
- [ ] **Step 4: Commit** `test(e2e): pose modes` + `docs: pose modes`.

### Task 7: real-GPU acceptance (controller does this)

- [ ] Using a separate backend and frontend (scratch storage, ports 8020/3020, as in Phase 5 Task 14), make three scene pages with the precise mode and get each to pass. On one page also generate one 快速 image, switch between them, and check the badges and compose. Record times (including model switching between background and pose). Results go to `experiments/results/phase6_qwen_pose_ui_check_<ts>/NOTES.md`; commit.

---

## Self-Review record

- Spec coverage:
  - §4.1 switch, memory, fallback (Task 5);
  - §4.2 badges (Task 4 `pose_mode` + Task 5);
  - §4.3 largest component (Task 2);
  - §5 workflow (Task 1);
  - §5 prompt and §7 calibration (Task 3);
  - §6 API, 503, `pose_mode`, availability (Task 4);
  - §8 success criteria (Task 6, Task 7);
  - §10 author calls: 10.1 (Task 3), 10.2 (Task 2/3), 10.3 (Task 4 `availability`), 10.5 (Task 4 503 with no fallback), 10.6 (Task 4 variation recorded).
- Name consistency: `pose_mode` (request_json, `GenerationOut`), `mode` (API body), `PoseMode` (frontend), `POSE_WORKFLOWS`, `qwen_pose_prompt`, `pick_variation`, `largest_component`, `availability`.
