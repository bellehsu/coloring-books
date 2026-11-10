# Phase 9: all-Qwen generation and a simpler editor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chinese-language, Qwen-only generation with uploaded backgrounds, a new 故事 step with paste import, and a two-step page editor (構圖 → 補線), so outside testers can make a book without help.

**Architecture:** The backend keeps its shape: a FastAPI app with one SQLite-backed worker, and ComfyUI behind `ComfyEngine`. The engine shrinks to three Qwen routes. The style and mode switches, the sketch / ControlNet code and the legacy whole-page creation are removed. Uploads and the new story batch reuse the existing `Generation` and `Page` model; the only schema change is one additive column. The frontend editor collapses five tabs into two, adds one book-level route (`/books/[id]/story`), and keeps the existing hooks, savers and canvas components.

**Tech Stack:**
- Backend: Python 3.11, FastAPI, SQLModel and Alembic on SQLite, Pillow, pytest.
- Frontend: Next.js, React, TypeScript, TanStack Query, vitest, Playwright.
- Generation: ComfyUI with ComfyUI-GGUF (Qwen-Image-Edit-2511 Q4_K_M plus Lightning).

**Spec:** `docs/superpowers/specs/2026-11-10-all-qwen-simplify-design.md` (read it alongside this plan; § numbers below refer to it).
**Visual reference:** mockup https://claude.ai/artifact/LkcDsyxEM5VXAmUKPinEyB. Boards: 角色, 故事, 貼上整本故事, 構圖, 補線與完成, 手機.

## Global Constraints

- Work in the worktree `/ai-data/repos/coloring-books-phase9` on branch `phase9-all-qwen`. Leave the main checkout alone: another session uses it, and its dev servers run on ports 8000 and 3000.
- Setup before Task 1:
  - Copy the gitignored config into the worktree: `cp /ai-data/repos/coloring-books/config/settings.yaml config/` and `cp /ai-data/repos/coloring-books/scripts/dev.env scripts/` (if present).
  - Run `cd frontend && npm ci`.
- Backend tests: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest tests -q`. The ROS `PYTHONPATH` breaks collection, so always unset it.
- Frontend unit tests: `cd frontend && npx vitest run`.
- e2e: `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npx playwright test`.
- API types: after any backend schema change, run `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run gen:api` and commit `openapi.json` and `schema.d.ts`.
- ComfyUI tests run against mocks (`tests/fake_comfy.py`, `tests/fake_engine.py`) and never need a GPU.
- Do not guess ComfyUI node names or inputs: check them against the running ComfyUI's `/object_info` (port 8188).
- **GPU runs (Tasks 1 and 19):** ask the user first, because the GPU is sometimes lent out. Afterwards run `POST http://127.0.0.1:8188/free` with `{"unload_models": true, "free_memory": true}`.
- No destructive migration. Old columns (`sketch_path`, `controlnet_strength`) and old rows stay.
- Paths only come from `config/settings.yaml` or `APP_*` env vars, never hard-coded.
- Copy strings are exactly as written in the spec and this plan (Traditional Chinese, full-width punctuation).
- Field limits stay as they are: scene ≤ 800 characters (`ScenePromptStr`), action 1–300 characters (`ActionStr`).
- Commit after every task; messages follow the repo style (`feat(api): …`, `docs: …`, `exp: …`). End each message with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XPLcrdHZRcJ3TZt5Bcr49V
  ```
- **Stop points (CLAUDE.md work rules):**
  - After Task 1, if any gate criterion fails: stop and report to the user.
  - After Task 19: write the phase report (done items, how to test, what the user must do) and wait.

## Review Focus

1. **Uploading an extreme aspect ratio or a small image** (a 4000×800 panorama, or a 64×64 icon). Expect a centred cover crop to the page size, never stretched, with no 500 error. Test in Task 7.
2. **Auto-compose racing a page switch** (drag on page 2, switch to page 3 within the debounce). The composite must be made for page 2 only, and page 3 must not change. Test in Task 13.
3. **Pasted text in the forms people actually produce**: CRLF line endings, full-width digits (「第１頁」), extra blank lines, Markdown bold (`**場景：**`). It should parse the same as the clean form. Test in Task 16.
4. **「產生全部素材」 pressed twice quickly** (double click, or two tabs). Expect no duplicate jobs: the second call skips with 「背景產生中」 / 「角色動作產生中」. Test in Task 9.
5. **Text over the field limit in a paste** (a scene over 800 or an action over 300 characters, or an empty action). The row is flagged in the preview and nothing is cut or sent. Test in Task 16.

---

## Part A — Experiment gate

### Task 1: Qwen character and Chinese prompt experiment (§3)

**Files:**
- Create: `experiments/results/phase9_all_qwen_<YYYYMMDD_HHMM>/` (`run.py`, `results.csv`, `NOTES.md`, `processed/`)
- Read first: `/ai-data/repos/coloring-books/experiments/results/phase9_qwen_character_zh_20261110_1502/`. This is another session's spike in the main checkout. Copy whatever it has finished (script, SDXL baselines, any Qwen results) into the new folder and only run what is missing. Do not edit the original.

**Interfaces:**
- Produces, as constants recorded in `NOTES.md` for Task 2 and Task 4:
  - `QWEN_CHARACTER_TEMPLATE: str` with a `{description}` (and optional `{variation}`) slot;
  - `QWEN_CHARACTER_VARIATIONS: tuple[str, ...]`, possibly empty;
  - `QWEN_CHARACTER_SIZE: tuple[int, int]`, both multiples of 16;
  - a generalised `QWEN_POSE_TEMPLATE` with an `{action}` slot and no "scarf" or "ears";
  - the chosen prompt shape: the user's Chinese inside the English template, or a fully Chinese template.

- [ ] **Step 1:** Ask the user whether the GPU is free. Confirm `GET http://127.0.0.1:8188/object_info/TextEncodeQwenImageEditPlus` answers.
- [ ] **Step 2 — E1 (characters).** Six Chinese descriptions: an animal, a child, a vehicle or object character, one with clothing or accessories, and two more of your choice. Use 4 seeds each, through the `qwen_background` graph (no image1), with candidate templates. If the 4 candidates look alike, add variation phrases and rerun. Score each candidate as usable or not, and record whether the 4 are visibly different.
- [ ] **Step 3 — E1 (poses).** Take the best candidate per description, plus the existing bunny (`/ai-data/repos/coloring-books/storage/books/2/generations/51.png`). Run 2 actions each through `qwen_edit_pose_cutout`, using the generalised pose template. Score consistency 1–5 as in Phase 6.
- [ ] **Step 4 — E2 (Chinese).** Six scenes and six actions, each in Chinese and English, 2 seeds each. Backgrounds go through `qwen_background` with the current variation list; poses go through `qwen_edit_pose_cutout`. Try both prompt shapes. Compute line-art metrics with `render_base(img, (2244, 3272), LineDetail.STANDARD, 11)` → `qa.evaluate`, as in Phase 8.
- [ ] **Step 5 — Write `NOTES.md`.** Include tables and timings. Mark each gate criterion PASS or FAIL:
  - ≥ 2 of 4 usable candidates per description;
  - the 4 candidates visibly different;
  - mean pose consistency ≥ 3.5;
  - bunny ≥ 4.0;
  - Chinese content correct in ≥ 5 of 6 cases (or no worse than English where English misses);
  - Chinese gaps and tiny regions within 20% of English;
  - no people or animals in backgrounds.

  Then record the constants listed under Interfaces.
- [ ] **Step 6:** Free the GPU (`POST /free`). Commit with `git commit -m "exp: phase 9 all-Qwen gate (characters, Chinese prompts)"`.
- [ ] **Step 7 — Gate.** If any criterion fails, **stop**: report the results to the user with AskUserQuestion, giving options such as keeping SDXL for characters or relaxing a bar, and wait. If all pass, continue to Part B.

---

## Part B — All-Qwen backend

### Task 2: `qwen_character` workflow, prompts and the 3-route engine (§4.1, §4.7)

**Files:**
- Create: `comfy_workflows/qwen_character/workflow_api.json`, `comfy_workflows/qwen_character/manifest.yaml`
- Modify: `backend/app/services/prompts.py`, `backend/app/services/comfy_engine.py`, `backend/app/services/engine.py`, `backend/app/services/worker.py:40-100`
- Test: `tests/test_prompts.py`, `tests/test_comfy_engine.py`, `tests/test_worker.py`, `tests/test_workflow_registry.py`

**Interfaces:**
- Produces:
  - `prompts.qwen_character_prompt(description: str, variation: str | None = None) -> str`
  - `prompts.pick_character_variation(rng: random.Random) -> str | None` (returns None when the list is empty)
  - constants `QWEN_CHARACTER_TEMPLATE`, `QWEN_CHARACTER_VARIATIONS`, `QWEN_CHARACTER_SIZE`, all from Task 1
  - `QWEN_POSE_TEMPLATE` replaced by Task 1's generalised wording
  - `comfy_engine.WORKFLOWS: dict[GenKind, str] = {CHARACTER: "qwen_character", BACKGROUND: "qwen_background", POSE: "qwen_edit_pose_cutout"}`
  - `comfy_engine.RETIRED = "這種生成已停用"`
  - `GenerationRequest` loses `structure_image`, `structure_strength`, `pose_mode` and `background_style`
  - `encode_request(*, kind, prompt, negative_prompt, seed, width, height, character_image=None, variation=None, extra: dict | None = None) -> str`, where `extra` is merged into the JSON for provenance keys such as `pose_mode` and `background_style`
  - `decode_request` returns a `GenerationRequest` and raises `ValueError(RETIRED)` when the JSON has `pose_mode == "fast"`, `background_style == "detailed"` or `kind == "page"`

- [ ] **Step 1: Build the workflow.** Copy `comfy_workflows/qwen_background/` to `qwen_character/`. Set `name: qwen_character` and `display_name: "Qwen 角色參考圖（白底全身）"`; parameters `prompt`, `seed`, `width`, `height`, with defaults set to `QWEN_CHARACTER_SIZE`. Check the node classes against `/object_info`.
- [ ] **Step 2: Write the failing tests.**
  - `test_qwen_character_prompt_inserts_description`: Chinese text appears verbatim; the variation appears when given.
  - `test_engine_routes_three_kinds`: with a fake client, CHARACTER → `qwen_character`, BACKGROUND → `qwen_background`, POSE → `qwen_edit_pose_cutout`; the POSE values include an uploaded `character_image`; none of them have a `negative_prompt` key.
  - `test_engine_rejects_page_kind`: `EngineError` with message `"這種生成已停用"`.
  - `test_decode_request_rejects_retired`: for each of `{"pose_mode": "fast"}`, `{"background_style": "detailed"}` and `{"kind": "page"}`, `pytest.raises(ValueError, match="這種生成已停用")`.
  - `test_registry_loads_qwen_character`
- [ ] **Step 3:** Run `pytest tests/test_prompts.py tests/test_comfy_engine.py tests/test_worker.py tests/test_workflow_registry.py -q`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement.**
  - `_generate` builds `{"prompt", "seed", "width", "height"}`, plus `"character_image"` for POSE, from `WORKFLOWS[req.kind]`. Any other kind raises `EngineError(RETIRED)`.
  - Delete `PAGE_FIXED`, `page_params`, `_white_png`, `BACKGROUND_WORKFLOWS`, `POSE_WORKFLOWS`, `CHARACTER_WORKFLOW` and `PAGE_WORKFLOW`.
  - Keep `params["variation"]` recording for all three kinds.
  - Old tests that exercise SDXL routing: delete them, or rewrite them to expect `RETIRED`.
- [ ] **Step 5:** Run the same command. Expected: PASS. Then run the full backend suite; failures may only be in files Tasks 3–6 own (compose, books, pose_modes, background_styles, scheduling). List them in the commit body.
- [ ] **Step 6: Commit** with `git commit -m "feat(engine): qwen_character workflow and three Qwen-only routes"`.

### Task 3: Character candidates on Qwen (§4.1)

**Files:**
- Modify: `backend/app/api/books.py:29,152-208`
- Test: `tests/test_api_books.py`

**Interfaces:**
- Consumes (Task 2): `qwen_character_prompt`, `pick_character_variation`, `QWEN_CHARACTER_SIZE`, `encode_request(..., variation=...)`.
- Produces: `CHARACTER_SIZE = QWEN_CHARACTER_SIZE`. Character generations get `negative_prompt == ""`, and `request_json["variation"]` is set (it can be null).

- [ ] **Step 1: Write the failing tests.**
  - `test_character_candidates_use_qwen_prompt`: POST with `{"description": "一隻圓圓的小白兔", "count": 4}`. Every generation has `negative_prompt == ""`, `prompt` contains `一隻圓圓的小白兔`, `(width, height) == QWEN_CHARACTER_SIZE`, and seeds `base..base+3`.
  - `test_character_candidates_record_variation`: with a monkeypatched variation list `("A", "B")`, each request_json has `variation` in `{"A", "B"}`.
- [ ] **Step 2:** Run `pytest tests/test_api_books.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. Drop the `CHARACTER_NEGATIVE` import and the use of `character_prompt`, and pick the variation per candidate with `random.Random()`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(api): character candidates on Qwen"`.

### Task 4: Remove background styles and pose modes (§4.2, §4.4)

**Files:**
- Delete: `backend/app/services/background_styles.py`, `backend/app/services/pose_modes.py`, `tests/test_background_styles.py`, `tests/test_pose_modes.py`
- Modify:
  - `backend/app/api/compose.py` (`_enqueue_background`, `create_background`, `create_backgrounds_batch`, `create_pose`, `_last_simple_variation`)
  - `backend/app/api/schemas.py:113-135,217-238`
  - `backend/app/main.py:108-116`
  - `backend/app/services/prompts.py` (delete `background_prompt`, `pose_prompt`, `BACKGROUND_NEGATIVE`, `POSE_NEGATIVE` and `character_prompt` once they are unused)
- Test: `tests/test_api_compose.py`, `tests/test_main.py`

**Interfaces:**
- Produces:
  - `BackgroundCreate` stays as a model that accepts and ignores `style` (`model_config = ConfigDict(extra="ignore")`, with field `style: str | None = None`).
  - `PoseCreate.mode: str | None = None` is ignored.
  - New background rows record `extra={"background_style": "simple"}`; new pose rows record `extra={"pose_mode": "precise"}`.
  - `GenerationOut.pose_mode` and `GenerationOut.background_style` still come from `pose_mode_of` and `background_style_of`, so old rows keep their badges. Change the `BackgroundStyle` and `PoseMode` Literals to live in `schemas.py`.
  - `_enqueue_background(session, storage, worker, book, page) -> GenerationOut` (the sketch and style parameters are gone).
  - `/api/system/pose-modes` and `/api/system/background-styles` are removed (404).
  - A pose request when Qwen is unavailable: there is no 503 pre-check any more; the job fails at run time like any other.

- [ ] **Step 1: Write the failing tests.**
  - `test_background_ignores_style`: POST `/pages/{id}/backgrounds` with `{"style": "detailed"}` → 202; request_json has `background_style == "simple"`; `negative_prompt == ""`.
  - `test_batch_ignores_style`
  - `test_pose_ignores_mode`: `{"action": "揮手", "mode": "fast"}` → request_json `pose_mode == "precise"`; prompt == `qwen_pose_prompt("揮手", <variation>)`.
  - `test_system_mode_endpoints_removed`: both return 404.
  - `test_old_rows_keep_badges`: a stored generation with request_json `{"pose_mode": "fast"}` → `GenerationOut.pose_mode == "fast"`.
- [ ] **Step 2:** Run `pytest tests/test_api_compose.py tests/test_main.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement and delete the dead code. In `compose.py`, also remove the sketch reading: `_read_sketch`, `sketch_bytes`, the 「草圖檔案遺失」 skip and the 409.
- [ ] **Step 4:** Run the full backend suite. Expected: PASS, apart from `test_scheduling.py` (owned by Task 5) and `test_api_pages.py` sketch and legacy tests (owned by Task 6).
- [ ] **Step 5: Commit** with `git commit -m "feat(api): one background style and one pose mode (Qwen)"`.

### Task 5: FIFO queue and retiring queued SDXL jobs (§4.7)

**Files:**
- Delete: `backend/app/services/scheduling.py`, `tests/test_scheduling.py`
- Modify: `backend/app/services/worker.py:103-235`, `backend/app/config.py:54-58`, `backend/app/main.py:59-66`
- Test: `tests/test_worker.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `GenerationWorker(...)` without `max_wait_s`, and without the `last_family` attribute.
  - `GenerationWorker.cancel_retired() -> int` is called once in `start()` after `requeue_interrupted()`. It sets status CANCELLED and error `"這種生成已停用"` on QUEUED rows where `decode_request` raises `ValueError(RETIRED)`, and returns the count.
  - `queue_position` is oldest first: order by `created_at`, then `id`.
  - `QueueSettings` keeps `retry_backoff_s`. A leftover `max_wait_s` key in settings is ignored, not an error.

- [ ] **Step 1: Write the failing tests.**
  - `test_worker_runs_oldest_first`: three QUEUED rows → `run_once` order equals creation order.
  - `test_cancel_retired_on_start`: QUEUED rows with `{"pose_mode": "fast"}` and with `{"kind": "page"}` → CANCELLED with that error; a Qwen row stays QUEUED.
  - `test_settings_ignore_max_wait`: settings YAML containing `queue: {max_wait_s: 180}` loads.
- [ ] **Step 2:** Run `pytest tests/test_worker.py tests/test_config.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. `_plan` becomes a plain ordered select. For `QueueSettings`, use `model_config = ConfigDict(extra="ignore")`.
- [ ] **Step 4:** Run the same command, then the full suite. Expected: PASS, apart from Task 6's pages tests.
- [ ] **Step 5: Commit** with `git commit -m "feat(worker): FIFO queue; cancel queued SDXL jobs on start"`.

### Task 6: Remove sketch and legacy creation; archive SDXL workflows; docs (§4.3, §4.6, §4.7, §4.10)

**Files:**
- Modify:
  - `backend/app/api/pages.py:184-224,276-368`: delete `upload_sketch`, `delete_sketch`, `get_sketch`, `create_candidates` and `select`.
  - `backend/app/api/lineart.py:277-305`: delete `reference-sketch`.
  - `backend/app/api/schemas.py`: delete `PageCandidatesCreate`. `PageUpdate.controlnet_strength` is accepted and ignored.
  - `PageOut.sketch_url` always returns `None`. Keep the field, so the generated types change only once.
- Move with `git mv`: `comfy_workflows/{sdxl_text2img,sdxl_background,sdxl_pose_cutout,route_a_sdxl_ipadapter_cn}` → `archive/comfy_workflows/`.
- Modify: `CLAUDE.md` (the 生成路線 section) and `README.md` (the models list and API table).
- Test: `tests/test_api_pages.py`, `tests/test_preflight.py`, `tests/test_api_lineart.py`

**Interfaces:**
- Produces: preflight only reports the Qwen workflows (`qwen_character`, `qwen_background`, `qwen_edit_pose_cutout`) and their models.

- [ ] **Step 1: Write the failing tests.**
  - `test_sketch_endpoints_removed`: `PUT`, `DELETE` and `GET /pages/{id}/sketch` → 404 or 405.
  - `test_legacy_creation_removed`: `POST /pages/{id}/candidates` and `/select` → 404 or 405.
  - `test_preflight_lists_only_qwen`: the registry built from `comfy_workflows/` has exactly the three Qwen names.
  - `test_legacy_page_still_exports`: a page whose `selected_generation_id` is a PAGE generation → `GET /pages/{id}/print.png` 200, and `/books/{id}/export.pdf` 200.
  - `test_legacy_page_redo_replaces_page_image`: set a background and pose on a legacy page, compose → `selected_generation_id` points at the new COMPOSITE.
- [ ] **Step 2:** Run `pytest tests/test_api_pages.py tests/test_preflight.py tests/test_api_lineart.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement, move the workflow folders, and delete the tests that covered the removed endpoints.
- [ ] **Step 4: Update CLAUDE.md.** Replace the Route C, Phase 5, Phase 6 and Phase 8 bullets under 生成路線 with one paragraph:
  - generation is all Qwen-Image-Edit-2511 (`qwen_character`, `qwen_background`, `qwen_edit_pose_cutout`);
  - SDXL, IP-Adapter and ControlNet are retired, with their workflows in `archive/comfy_workflows/`;
  - pages are composed as background (Qwen or upload) + pose → Pillow → xdog;
  - pointers to the spec and to Phase 1–9 experiments.

  Update the README models list (Qwen files plus BiRefNet) and the API table.
- [ ] **Step 5:** Run the full backend suite. Expected: PASS, all green.
- [ ] **Step 6: Commit** with `git commit -m "feat: retire sketch, legacy page creation and SDXL workflows"`.

---

## Part C — Upload, story data and finishing

### Task 7: Upload a background (§4.2)

**Files:**
- Create: `backend/app/services/upload_bg.py`
- Modify: `backend/app/api/compose.py` (new endpoint)
- Test: `tests/test_upload_bg.py`, `tests/test_api_compose.py`

**Interfaces:**
- Produces:
  - `upload_bg.cover_crop(img: Image.Image, size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int, int, int]]`: scale to cover with LANCZOS, then centre-crop. Returns the image and the crop box in scaled coordinates.
  - `POST /api/pages/{page_id}/backgrounds/upload`, multipart `file` → 201 `PageOut`. It creates a BACKGROUND generation with SUCCEEDED, `engine="upload"`, `workflow_name=None`, `seed=0`, `user_text=""`, `prompt=""`, `negative_prompt=""`, and `params_json={"filename", "original_size": [w, h], "crop_box": [l, t, r, b]}`. `request_json` is `encode_request(kind=BACKGROUND, ..., extra={"source": "upload"})`. The row is then selected via `_pick(...)`.
  - `GenerationOut` gains `source: Literal["generated", "upload"]` (`"upload"` when `engine == "upload"`).

- [ ] **Step 1: Write the failing tests.**
  - `test_cover_crop_panorama`: a 4000×800 image to (896, 1152) → output exactly (896, 1152); crop box centred (|left − (scaled_w − 896)/2| ≤ 1).
  - `test_cover_crop_small_upscales`: 64×64 → (896, 1152).
  - `test_cover_crop_never_stretches`: a checkerboard source keeps square cells (the aspect ratio of one cell is within ±2%).
  - `test_upload_background_creates_selected_generation`: page's `background_generation_id` equals the new id; `source == "upload"`; the image is 896×1152 (or the book's `generation_size`).
  - `test_upload_rejects_bad_file`: text bytes → 400 with the `ImageRejected` message; 11 MB → 400.
  - `test_upload_resets_composite_like_pick`: page with a COMPOSITE selected → after upload, `selected_generation_id is None` and `status == "draft"`.
  - `test_upload_on_legacy_page_keeps_page_image`: a legacy PAGE selection stays until compose.
- [ ] **Step 2:** Run `pytest tests/test_upload_bg.py tests/test_api_compose.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. Validate with `normalize_png(data, max_bytes=10_000_000)`; the size comes from `compute_layout(book.paper_size, book.print_mode).generation_size`. Write the file before committing the row, and delete it on rollback, following the pattern in `confirm_compose`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(api): upload a page background (cover crop)"`.

### Task 8: `page.action_prompt` (§4.8)

**Files:**
- Create: `backend/migrations/versions/0005_page_action_prompt.py`
- Modify: `backend/app/models.py` (Page), `backend/app/api/schemas.py` (PageCreate, PageUpdate, PageOut)
- Test: `tests/test_migrations.py`, `tests/test_api_pages.py`

**Interfaces:**
- Produces:
  - `Page.action_prompt: str = ""`, a NOT NULL column with server default `''`.
  - `PageCreate.action_prompt: ActionPromptStr = ""` and `PageUpdate.action_prompt: ActionPromptStr | None`, where `ActionPromptStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]`.
  - `PageOut.action_prompt: str`.

- [ ] **Step 1: Write the failing tests.**
  - `test_migration_0005_adds_action_prompt`: upgrade from 0004 → the column exists and existing rows have `''`; downgrade removes it.
  - `test_patch_action_prompt`: PATCH `{"action_prompt": "抱著一本書往上爬"}` → returned and persisted; 301 characters → 422.
  - `test_create_page_with_texts`: POST `/books/{id}/pages` with scene and action → both stored.
- [ ] **Step 2:** Run `pytest tests/test_migrations.py tests/test_api_pages.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement, using the 0004 `batch_alter_table` style.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(db): page.action_prompt"`.

### Task 9: 「產生全部素材」 batch and automatic selection (§4.8)

**Files:**
- Modify: `backend/app/api/compose.py` (new endpoint, reusing `_BATCH_LOCK` and the skip logic), `backend/app/services/worker.py` (`run_once` success path), `backend/app/api/schemas.py`
- Test: `tests/test_api_compose.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: Task 8's `Page.action_prompt`; Task 4's `_enqueue_background(session, storage, worker, book, page)`.
- Produces:
  - `POST /api/books/{book_id}/materials/batch` → 202 `BatchMaterialsOut{queued: list[GenerationOut], skipped: list[BatchSkip]}`. `BatchSkip` gains `kind: Literal["background", "pose"]`.
  - Background skip reasons are unchanged (「沒有場景描述」, 「已有背景」, 「背景產生中」). Pose reasons are 「沒有動作描述」, 「已有角色動作」, 「角色動作產生中」 and 「請先設定角色」.
  - Legacy pages skip both kinds with 「已有背景」 / 「已有角色動作」.
  - `create_pose`'s body becomes a helper `_enqueue_pose(session, storage, worker, book, page, action: str) -> GenerationOut`, used by both the single and the batch endpoint.
  - Remove `POST /books/{id}/backgrounds/batch` (the frontend switches to the materials endpoint in Task 15).
  - The worker's success path calls `autoselect(session, gen)`, from `backend/app/api/compose.py` or a small `services/autoselect.py`. For a BACKGROUND or POSE row whose page slot is None, it performs the same UPDATE as `_pick`. It never replaces a selected slot, and never raises: it logs on failure.

- [ ] **Step 1: Write the failing tests.**
  - `test_materials_batch_counts`: 3 pages (both texts; scene only; selected background + action) → queued kinds `[bg, pose, bg, pose]` with skips `{page2: pose 「沒有動作描述」, page3: background 「已有背景」}`.
  - `test_materials_batch_twice_no_duplicates`: call twice → the second call queues 0, and the skips say 「背景產生中」 and 「角色動作產生中」.
  - `test_materials_batch_needs_character`: no character → poses skipped 「請先設定角色」; backgrounds still queued.
  - `test_autoselect_fills_empty_slot`: worker success on a BACKGROUND for a page with no background → `background_generation_id` is set.
  - `test_autoselect_never_replaces`: a page already has a background → it is unchanged.
- [ ] **Step 2:** Run `pytest tests/test_api_compose.py tests/test_worker.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full backend suite. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(api): generate all materials (backgrounds + poses) and auto-select"`.

### Task 10: Pass without a typed reason (§4.5)

**Files:**
- Modify: `backend/app/api/review.py:35,58-104`
- Test: `tests/test_api_review.py`

**Interfaces:**
- Produces:
  - `POST /pages/{id}/pass` accepts `{}` (`force_reason` optional, as today).
  - With QA errors and no reason, it stores `forced_pass_reason = f"使用者確認完成（{n} 個缺口）"`, where `n = lin.qa.gap_count`. It returns 200 instead of 409.
  - A reason that is given is still stored as given.

- [ ] **Step 1: Write the failing tests.**
  - `test_pass_with_gaps_auto_reason`: QA with 3 gaps, body `{}` → 200; `forced_pass_reason == "使用者確認完成（3 個缺口）"`.
  - Rewrite the old 409 test to the new behaviour.
- [ ] **Step 2:** Run `pytest tests/test_api_review.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the same command, then the full suite. Expected: PASS.
- [ ] **Step 5:** Regenerate the API types (`npm run gen:api`). Commit with `git commit -m "feat(review): finish a page with gaps after one confirmation"`, including `frontend/src/api/openapi.json` and `schema.d.ts`.

---

## Part D — Frontend

### Task 11: Remove switches and sketch UI; hooks for the new endpoints (§4.2–4.4)

**Files:**
- Delete:
  - `frontend/src/lib/poseMode.ts` and `bgStyle.ts`, with their tests;
  - `frontend/src/features/editor/SketchCanvas.*`, `sketchSaver.*`, `referenceSketch.ts`, `BackgroundStage.*`, `batchBackgrounds.*`;
  - `frontend/e2e/bgstyle.spec.ts`.
- Modify:
  - `frontend/src/api/hooks.ts`: delete `usePoseModes`, `useBackgroundStyles`, `usePageCandidates` and `useBatchBackgrounds`. Add `useUploadBackground(bookId)`, `useBatchMaterials(bookId)` and `usePatchPageText(bookId)` (reuse `useUpdatePage` if it fits).
  - `frontend/src/api/client.ts`: an `upload` helper that posts FormData and returns JSON, with errors as `ApiError`.
  - `BackgroundPanel.tsx` and `PosePanel.tsx`: remove the Segmented switches, the strength slider and the sketch sections.
  - `HistoryTray.tsx`: show the 「上傳」 badge when `source === "upload"`; hide the 簡潔/細緻/精準/快速 badges on new rows (rows created after the switch report precise/simple, so hide those two values).
- Test: `frontend/src/api/hooks.test.tsx`, `HistoryTray.test.tsx`, `BackgroundPanel.test.tsx`, `PosePanel.test.tsx`

**Interfaces:**
- Produces:
  - `useUploadBackground(bookId: number)`: a mutation `{pageId: number; file: File} → Page` that invalidates pages and generations.
  - `useBatchMaterials(bookId: number)`: a mutation `() → BatchMaterialsOut`.

- [ ] **Step 1: Write the failing tests.**
  - `useUploadBackground posts multipart to /pages/{id}/backgrounds/upload`
  - `HistoryTray shows 上傳 badge for source upload`
  - `HistoryTray hides 精準 and 簡潔 badges`
  - `BackgroundPanel has no 背景風格 radiogroup and no 構圖強度 slider`
  - `PosePanel has no 產生方式 radiogroup`
- [ ] **Step 2:** Run `npx vitest run src/api src/features/editor`. Expected: FAIL.
- [ ] **Step 3:** Implement and delete. Then fix the compile errors: `npx tsc --noEmit` must be clean.
- [ ] **Step 4:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(editor): drop style/mode switches and the sketch board"`.

### Task 12: Two-step editor shell: ① 構圖 and ② 補線 (§4.5, mockup boards 構圖 and 補線與完成)

**Files:**
- Create: `frontend/src/features/editor/ComposeWorkspace.tsx` (the right panel of 構圖: 背景 card and 角色動作 card) with `.module.css`
- Modify:
  - `StageHeader.tsx`: `EditorMode = "compose" | "line"`, with `MODES = [{compose, "① 構圖"}, {line, "② 補線"}]`.
  - `EditorPage.tsx`: stage and panel selection; the stage for compose is always `ComposeStage`.
  - `Inspector.tsx` and `lib/checklist.ts`: three items.
- Test: `StageHeader.test.tsx`, `EditorPage.test.tsx`, `lib/checklist.test.ts`, `ComposeWorkspace.test.tsx`

**Interfaces:**
- Produces:
  - `modeBlockers(g)`: `line` is blocked with 「請先擺好角色位置」 when `selectedId == null`; `compose` is never blocked.
  - `isEditorMode` maps old URL values: `background`, `pose` → `compose`; `qa` → `line`. Export `normalizeMode(v: string | null): EditorMode`.
  - `checklist(page, qa, opts)` → `CheckKey = "compose" | "line" | "passed"`, with labels 「構圖」「補線」「完成」 and notes:
    - compose: 「還沒選背景」 / 「還沒選角色動作」 / 「已合成」, or warn 「待更新」;
    - line: 「N 個缺口」 / 「線條都接好了」;
    - passed: 「完成」 / 「完成（N 個缺口）」.
  - `<ComposeWorkspace bookId page background pose onPickBackground onPickPose />` renders:
    - the 背景 card: history radio thumbnails, 「換一張」 (`useGenerateBackground`), 「上傳背景圖」 (`useUploadBackground`, file input `accept="image/png,image/jpeg,image/webp"`), the hint 「線條簡單的插畫效果最好；照片擷取的線稿可能很亂，需要多補線。」, and a `<details>` scene editor;
    - the 角色動作 card: history thumbnails, 「換一張」 (`useGeneratePose` with the page's `action_prompt`, or the latest pose's `user_text`), a `<details>` action editor and quick chips.

- [ ] **Step 1: Write the failing tests.**
  - `StageHeader renders exactly two tabs ① 構圖 and ② 補線`
  - `normalizeMode maps background/pose→compose and qa→line`
  - `checklist returns three items` with the notes above for: no background; both selected with no composite; composite with 3 gaps; passed with a forced reason.
  - `ComposeWorkspace upload calls useUploadBackground and shows the hint`
  - `ComposeWorkspace 換一張 for pose uses page.action_prompt`
- [ ] **Step 2:** Run `npx vitest run src/features/editor src/lib`. Expected: FAIL.
- [ ] **Step 3:** Implement. Reuse the HistoryTray thumbnails, or extract a `Thumbs` component from it. Remove `HistoryTray` from the bottom of the editor if the cards replace it (the mockup has no bottom tray).
- [ ] **Step 4:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(editor): two-step page editor (構圖 / 補線)"`.

### Task 13: Auto-compose (§4.5)

**Files:**
- Create: `frontend/src/features/editor/autoCompose.ts`
- Modify: `EditorPage.tsx`, `ComposeStage.tsx`, `ComposePanel.tsx` (delete it when nothing is left in it)
- Test: `autoCompose.test.ts`, `EditorPage.test.tsx`

**Interfaces:**
- Consumes: `placementSaverFor(qc)`, `useCompose(bookId)`, `useEdits(pageId)`.
- Produces:
  - `createAutoCompose(opts: { delayMs: number; compose: (pageId: number) => Promise<void>; hasLineEdits: (pageId: number) => boolean; confirm: () => Promise<boolean> }): { schedule(pageId: number): void; flush(pageId: number): Promise<void>; status(pageId: number): "idle" | "pending" | "running" | "error" }`.
  - The default `delayMs` is 600.
  - A later `schedule` for the same page resets the timer.
  - Each page has its own timer, so pages never cross.
  - If `hasLineEdits(pageId)` is true, the first compose for that page in this session awaits `confirm()`; on false it does nothing and the status goes back to idle.
- Triggers that call `schedule(current.id)`: drag end, resize end, 「水平翻轉」, 「重設位置」, and picking a background or pose (including upload and auto-select arriving while both slots are filled).
- The status line under the stage reads 「✓ 線稿已自動更新」, 「更新中…」 or 「更新失敗，重試」 (a button that calls `flush`).
- The confirmation is a `Dialog` titled 「清掉補過的線？」 with body 「移動角色會清掉這頁補過的線，要繼續嗎？」 and buttons 「繼續」 / 「取消」.
- Remove the 「確認合成」 button and the switch-to-line-mode on compose.

- [ ] **Step 1: Write the failing tests** (using fake timers).
  - `debounces bursts into one compose`
  - `separate pages compose independently` (schedule page 2, then page 3 within 600 ms → compose called with 2 and with 3, never with 3 for page 2's change). This is Review Focus 2.
  - `asks once when line edits exist; cancel skips compose`
  - `error status and flush retries`
  - In `EditorPage.test.tsx`: `drag end triggers compose without a confirm button`.
- [ ] **Step 2:** Run `npx vitest run src/features/editor/autoCompose.test.ts src/features/editor/EditorPage.test.tsx`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(editor): auto-compose on placement and material changes"`.

### Task 14: 補線 panel: check, 線條多寡, 角色對嗎, 完成這一頁 (§4.5)

**Files:**
- Create: `frontend/src/features/editor/FinishPanel.tsx` (it replaces `QAPanel.tsx`, `LinePanel.tsx` and `QACompare.tsx`; delete those three, with their tests)
- Modify: `LineFixStage.tsx` (gap circles overlay), `EditorPage.tsx`
- Test: `FinishPanel.test.tsx`, `LineFixStage.test.tsx`

**Interfaces:**
- Consumes: `useQA`, `useAutofix`, `useCharacterCheck`, `usePass`, `useUnpass`, `onPatch` (line detail and width).
- Produces: `<FinishPanel book page qa generation onPatch onGoCompose />`, with these cards in order:
  1. **Check card**:
     - With gaps: 「還有 N 個缺口」, the sentence 「橘色圈起來的地方線條沒有接起來，小朋友塗色時顏色會漏出去。」, the button 「自動補線」, and the hint 「自動補不好的，用「補線」筆在畫面上畫一筆接起來。」.
     - Without gaps: 「✓ 線條都接好了」 and 「每個區塊都是封閉的，可以放心塗色。」.
     - Non-gap warnings appear as a small list.
  2. **「線條多寡」**: Segmented 簡單／標準／細緻 (today's `line_detail`), plus `<details><summary>進階：線寬</summary>` with the existing width slider.
  3. **「角色看起來對嗎？」**: the hint 「臉、耳朵、衣服跟角色一樣，而且沒有多出別的角色。」.
     - 「對」 → `characterCheck("consistent")`.
     - 「不太像，換一張」 → `characterCheck("inconsistent")`, then `onGoCompose()`.
  4. **「完成這一頁 ✓」**:
     - It is disabled with the note 「請先回答角色對不對」 unless the character check is consistent.
     - With gaps, it opens a Dialog with body 「還有 N 個缺口，小朋友塗色時顏色可能漏出去。確定完成嗎？」 and buttons 「完成」 / 「再補補看」, then calls `pass({})`.
     - A passed page shows 「已完成」 and 「取消完成」 (unpass).
- `LineFixStage` draws orange circles at the QA gap positions. Use the `QA` data the overlay endpoint already uses: check `lineart.py:134` for the gap list or the overlay PNG, and draw from the list when available, otherwise layer the overlay PNG.

- [ ] **Step 1: Write the failing tests.**
  - `shows gap count and autofix`
  - `no gaps shows 線條都接好了`
  - `完成 disabled until character consistent`
  - `完成 with gaps asks once then passes with empty body`
  - `不太像 records inconsistent and goes to compose`
  - `line width slider is inside 進階 details`
  - `LineFixStage renders one circle per gap`
- [ ] **Step 2:** Run `npx vitest run src/features/editor`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(editor): merged 補線 + finish panel"`.

### Task 15: 故事 page and the four-step book bar (§4.8, mockup board 故事)

**Files:**
- Create:
  - `frontend/src/app/books/[bookId]/story/page.tsx`
  - `frontend/src/features/story/StoryPage.tsx`, `StoryRow.tsx` and `.module.css`
- Modify:
  - `frontend/src/components/shell/BookBar.tsx`: steps 角色 → 故事 → 頁面 N/M → 匯出. `current` gains `"story"`. 故事 is disabled with 「請先設定角色」 until the character is set.
  - `frontend/src/features/character/CharacterStudio.tsx`: after a character is set, the main button reads 「用這個角色，開始寫故事 →」 and links to `/books/{id}/story`.
  - `Filmstrip.tsx`: remove 「產生所有背景」.
- Test: `StoryPage.test.tsx`, `BookBar.test.tsx`, `CharacterStudio.test.tsx`, `Filmstrip.test.tsx`

**Interfaces:**
- Consumes: `usePages`, `useUpdatePage`, `useCreatePage`, `useUploadBackground`, `useBatchMaterials`, `useGenerations`.
- Produces: `<StoryPage bookId />`, with one `<StoryRow>` per page:
  - the 背景 Segmented 「AI 產生」／「上傳圖片」, kept as local state, starting at 上傳圖片 when the selected background's `source === "upload"`;
  - the scene textarea, saved on blur plus a 600 ms debounce through `useUpdatePage`;
  - or the upload zone, which calls `useUploadBackground` on select and shows the filename and 「（已裁成頁面比例）」;
  - the action textarea, saved to `action_prompt`, with the quick chips 站著、揮手、跳起來、拿著東西、看書;
  - status thumbnails for the background and pose: the image, or 「排隊中」 / 「生成中 N%」 / 「失敗，重試」.
- Header buttons: 「貼上整本故事」 (it opens Task 16's dialog), 「＋ 新增一頁」, and 「產生全部素材（還有 K 張）」.
  - K = pages with a scene, no background, AI source and nothing queued, plus pages with an action, no pose and nothing queued.
  - The button is disabled at 0 and calls `useBatchMaterials`.
  - Afterwards a toast summarises the skips.
- Banner (role=status) while jobs are queued or running: 「正在產生素材，可以先去「頁面」擺已經好的頁面，不用等全部完成。」

- [ ] **Step 1: Write the failing tests.**
  - `renders one row per page with scene and action`
  - `toggle to 上傳圖片 shows upload zone and uploads on select`
  - `typing saves scene and action_prompt after debounce`
  - `batch button counts missing materials and is disabled at 0`
  - `BookBar shows four steps with 故事 disabled before a character`
  - `CharacterStudio links to story after selection`
- [ ] **Step 2:** Run `npx vitest run src/features/story src/components/shell src/features/character`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(story): write every page, then generate all materials"`.

### Task 16: Paste import 「貼上整本故事」 (§4.8, mockup board 貼上整本故事)

**Files:**
- Create: `frontend/src/lib/storyImport.ts`, `frontend/src/features/story/ImportDialog.tsx`, and `frontend/src/features/story/aiPrompt.ts` (the copy-prompt constant)
- Test: `frontend/src/lib/storyImport.test.ts`, `ImportDialog.test.tsx`

**Interfaces:**
- Produces:
  - `parseStory(text: string): { pages: ParsedPage[]; errors: ParseError[] }`, where:
    - `ParsedPage = { n: number; scene: string; action: string }`;
    - `ParseError = { n: number | null; excerpt: string; reason: string }`.
  - `planImport(parsed: ParsedPage[], existing: { position: number; scene_prompt: string; action_prompt: string }[]): ImportRow[]`, where:
    - `ImportRow = { n: number; scene: string; action: string; fill: { scene: boolean; action: boolean }; create: boolean; problems: string[] }`;
    - `existing` is indexed by position order, so page n maps to the n-th page.
  - `MAX_IMPORT_PAGES = 40`, `SCENE_MAX = 800`, `ACTION_MAX = 300`.
  - `AI_PROMPT(pages: number): string`.

- Parsing rules:
  - Normalise first: CRLF → LF, full-width digits → ASCII, and strip `*`, `#` and `>` Markdown markers at the start of a line or around labels.
  - Headings, as a regex on a trimmed line: `^(第\s*\d+\s*頁|\d+\s*[.、．]|P\s*\d+|Page\s*\d+)\s*[:：]?\s*$`, case-insensitive. The heading may also be followed by text on the same line, in which case the rest of the line is parsed as labels.
  - Labels: `^(場景|背景)\s*[:：]\s*(.*)$` → scene; `^(動作|角色)\s*[:：]\s*(.*)$` → action. A label's text continues onto following unlabelled lines until the next label or heading.
  - Without headings, blank-line-separated blocks are pages 1, 2, 3, ….
  - TSV mode is used when ≥ 1 line contains a tab and no heading or label matches. Each line gives column 1 as the scene and column 2 as the action; a first row containing 場景 or 背景 is a header and is skipped.
  - A block with neither label is an error: reason 「找不到「場景：」或「動作：」」.
  - Pages past 40 are errors: 「超過 40 頁，這次不匯入」.
- `planImport` problems:
  - scene longer than 800 → 「場景超過 800 字」;
  - action longer than 300 → 「動作超過 300 字」.

  A row with problems is not applied. `fill.x` is true only when the parsed value is non-empty and the existing field is empty (fill-empty-only). `create` is true when n is past the existing page count.
- `<ImportDialog bookId open onClose />`:
  - A textarea, and a live preview table with columns 頁, 場景, 動作 and 結果. 結果 is one of 「填入場景、動作」, 「填入動作」, 「已有內容，略過」, 「新增這一頁」, or a red error.
  - A summary line: 「N 段：新增 X 頁、填入 Y 格、略過 Z 格、W 段看不懂」.
  - 「複製給 AI 的指令」 → `navigator.clipboard.writeText(AI_PROMPT(n))`, followed by a toast 「已複製」.
  - The apply button reads 「匯入（新增 X 頁、填入 Y 格）」. It creates pages in order with `useCreatePage({scene_prompt, action_prompt})`, then PATCHes the fills, and finally invalidates pages. It never deletes pages.
- `AI_PROMPT` text (exact):
  ```
  請把下面的故事改寫成 {pages} 頁的兒童著色繪本分鏡。每一頁只寫兩行，格式如下：

  第1頁
  場景：（這一頁的背景，只寫地點和物品，不要出現任何人物或動物）
  動作：（主角在這一頁做什麼，一句話）

  場景不超過 60 字，動作不超過 30 字，用繁體中文。只輸出分鏡，不要其他說明。

  故事：
  ```

- [ ] **Step 1: Write the failing tests** in `storyImport.test.ts`.
  - `parses labelled blocks with 第N頁 headings`
  - `accepts 1. / 1、 / P1 / Page 1 headings`
  - `accepts 背景/角色 labels and half-width colons`
  - `handles CRLF, full-width digits, extra blank lines and **bold** labels` (Review Focus 3)
  - `multi-line label text continues until next label`
  - `blocks without headings become consecutive pages`
  - `TSV with header row`
  - `block without labels is an error with excerpt`
  - `more than 40 pages flags the rest`
  - `planImport fills only empty fields`
  - `planImport creates pages past the existing count`
  - `planImport flags over-long scene (801) and action (301) and does not apply them` (Review Focus 5)
- [ ] **Step 2: Write the failing tests** in `ImportDialog.test.tsx`.
  - `preview summary counts`
  - `apply creates then patches and never deletes`
  - `copy prompt writes AI_PROMPT(n) to clipboard`
- [ ] **Step 3:** Run `npx vitest run src/lib/storyImport.test.ts src/features/story`. Expected: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 6: Commit** with `git commit -m "feat(story): paste import with preview (fill empty only)"`.

### Task 17: GPU status pill (§4.9)

**Files:**
- Modify: `frontend/src/components/shell/QueuePill.tsx`, plus a pure helper in `frontend/src/lib/eta.ts`
- Test: `QueuePill.test.tsx`, `lib/eta.test.ts`

**Interfaces:**
- Consumes:
  - `useQueue()`, whose response has `queued`, `running_id` and `state`. Check `/api/system/queue` and add fields to the backend `QueueStatus` only if the ETA needs them.
  - `useGenerations` for recent `elapsed_s`.
- Produces:
  - `estimateSeconds(pending: { kind: "character" | "background" | "pose" }[], recent: { kind: string; elapsed_s: number }[]): number`. It uses the median of the last 10 per kind, with fallbacks character 20, background 20 and pose 25 seconds.
  - The pill text:
    - 「GPU 閒置」 when the queue is empty;
    - 「生成中 n/m · 剩約 X 分鐘」, with X = ceil(seconds / 60), and 「剩不到 1 分鐘」 under 60 seconds;
    - 「GPU 就緒」 for 5 minutes after the last job, then 閒置;
    - the existing error texts (「生成引擎無法連線」, 「生成服務已停止」) stay.

    m counts jobs since the queue was last empty. Keep that in component state; it does not need to survive a reload.

- [ ] **Step 1: Write the failing tests.**
  - `estimateSeconds uses medians and fallbacks`
  - `pill shows 閒置 / 生成中 2/6 · 剩約 2 分鐘 / 就緒 then 閒置` (with fake timers)
- [ ] **Step 2:** Run `npx vitest run src/components/shell src/lib/eta.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `npx vitest run`. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(shell): GPU status pill with ETA"`.

### Task 18: Phone layout and e2e (§4.5 phone width; mockup board 手機)

**Files:**
- Modify: `EditorPage.module.css`, `Filmstrip.tsx` and its CSS (at ≤ 640 px the list becomes a 「第 N 頁 / M ▾」 select), `ComposeWorkspace` CSS (compact cards under the stage), and `frontend/src/features/story/*.module.css`
- Modify: `scripts/dev_fake_backend.py`. Support the new endpoints (upload, materials batch, `action_prompt`, pass without reason), with fake generation finishing instantly or after a short delay, and auto-select.
- Modify / create e2e: `frontend/e2e/flow.spec.ts` (rewrite for the new flow), `frontend/e2e/rwd.spec.ts`, `frontend/e2e/batch.spec.ts` (materials), and a new `frontend/e2e/story.spec.ts`

**Interfaces:**
- Consumes everything above. Nothing new is produced.

- [ ] **Step 1: Write the failing e2e tests.**
  - `story.spec.ts`: paste 3 pages (one with CRLF and full-width digits) → preview → import → 「產生全部素材」 → thumbnails appear.
  - `flow.spec.ts`: character → story → page 1: drag the character, see 「✓ 線稿已自動更新」 → ② 補線 → 「自動補線」 → 「對」 → 「完成這一頁 ✓」 → page badge 完成.
  - `flow.spec.ts` upload: in 構圖, 「上傳背景圖」 with a 4000×800 PNG fixture → the stage shows the cropped background with the 「上傳」 badge.
  - `rwd.spec.ts` at 390×844: the editor has the page select dropdown and no horizontal scroll (`document.documentElement.scrollWidth <= 390`); the story rows stack.
- [ ] **Step 2:** Run `npx playwright test`. Expected: FAIL.
- [ ] **Step 3:** Implement the CSS and the fake backend.
- [ ] **Step 4:** Run `npx playwright test`, then `npx vitest run`, then `npx tsc --noEmit`, then `npm run lint`. Expected: all PASS or clean.
- [ ] **Step 5: Commit** with `git commit -m "feat: phone layout and e2e for the new flow"`.

---

## Part E — Acceptance

### Task 19: Real-GPU acceptance and the phase report (§5)

**Files:**
- Create: `experiments/results/phase9_acceptance_<YYYYMMDD_HHMM>/` (`NOTES.md`, screenshots, timings)

- [ ] **Step 1: Set up the backend.**
  - Ask the user whether the GPU is free.
  - Copy the real DB and storage: `cp -a /ai-data/repos/coloring-books/storage /ai-data/repos/coloring-books-phase9/storage-accept`.
  - Start the backend on port 8001 with `APP_PATHS__STORAGE_DIR` and `APP_PATHS__DATABASE` pointing at `storage-accept`. It runs migration 0005 and cancels any queued SDXL jobs.
  - Start the frontend on port 3001.
- [ ] **Step 2: Old data still works.** Open every existing book. Export book 1 and book 2 to PDF (200). The legacy book 1 pages 1–2 open and show line art.
- [ ] **Step 3: New book, all inputs in Chinese.** Make a new 3-page book:
  1. Create the character (4 candidates) and pick one.
  2. In 故事, paste a 3-page storyboard; set page 3 to 上傳圖片 (any simple illustration); press 「產生全部素材」 once.
  3. For each page: 構圖 (drag), 補線 (自動補線 plus any manual fixes), 「對」, 「完成這一頁 ✓」.
  4. Export the PDF.

  Record timings for each step against Phase 7 and 8, and take a screenshot of each step.
- [ ] **Step 3b: Redo a legacy page.** Take a copy of legacy book 1 page 1 through 構圖 with an uploaded background, and confirm the PAGE image is replaced.
- [ ] **Step 4: Clean up.** Free the GPU (`POST /free`), stop the servers, and delete `storage-accept`.
- [ ] **Step 5: Write `NOTES.md`.** Commit with `git commit -m "exp: phase 9 real-GPU acceptance"`.
- [ ] **Step 6: Phase report to the user, then stop.** Cover:
  - what was done;
  - how to test it;
  - what the user must do: review, merge, and optionally delete the SDXL, ControlNet, IP-Adapter and CLIP-ViT-H model files (about 12.8 GB) from `/ai-data/models/comfy`.

  Merging follows superpowers:finishing-a-development-branch after the user approves.
