# 清掉框外雜物（背景清理）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Qwen-Image-Edit pass that removes clutter outside a background's frame: automatic for 華麗相框 pages, and a 「清掉框外雜物」 button on portrait-mode pages.

**Architecture:** A cleanup is a BACKGROUND generation whose input is another background (new workflow `qwen_edit_cleanup`, no cutout). Pages gain `auto_cleanup` / `cleanup_prompt`; a successful AI background on an auto page is replaced by its cleanup before the page pipeline composes. Quota counts cleanups (reserved up front). The frontend adds the button and the pack data.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / Alembic; ComfyUI (mocked in tests); Next.js / React / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-background-cleanup-design.md`

## Global Constraints

- Branch `background-cleanup-2026-11-24`, main checkout (no worktree). Backend tests `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`; frontend `cd frontend && npx vitest run`, `npx tsc --noEmit`, `npx eslint <files>`; regenerate API types with `npm run gen:api`.
- Pages without `auto_cleanup` and without a button press behave exactly as today (no extra GPU job, same quota, same pipeline).
- GENERIC prompt (backend constant, verbatim): 「Remove every object outside the ornate frame (balloons, circles, clouds, hearts, animals, ground lines). Keep the ornate frame exactly the same: same lines, same shape, same position and size, and keep the inside of the frame empty white. Everything outside the frame is plain pure white. Black and white line art, clean black outlines.」
- CLOVER prompt (frontend pack data, verbatim): 「Delete the rectangular decorative border that runs along the four edges of the picture, including its corner ornaments. Only the four-leaf clover shaped frame in the middle remains, unchanged. The rest of the page is plain pure white. Black and white line art.」
- `cleanup_prompt` ≤ 600 chars. Exact copy: button 「清掉框外雜物」; not-portrait message 「有框的背景（肖像模式）才能清」; thumbnail badge 「清過」.
- Never guess ComfyUI node names/inputs: verify against the running ComfyUI `/object_info` (read-only GET; do not queue prompts). No model downloads.
- Quota owner = the book owner (same usage owner as the source background).

## Review Focus

- A cleanup's success must never trigger another cleanup (no chain), including on auto pages (Task 4 test).
- Background generation fails on an auto page → both reserved counts (background + cleanup) are refunded (Task 4 test).
- 「換一張」 on an auto page with only 1 count left → today's 429 message, nothing queued (Task 4 test).
- Changing `scene_prompt` via PATCH turns auto cleanup off even when other fields are sent in the same PATCH; sending the same text does not (Task 1 test).
- Uploaded backgrounds on auto pages are never auto-cleaned (Task 4 test).

---

### Task 1: data — page fields, source generation id

**Files:** Create `backend/migrations/versions/0016_background_cleanup.py`; Modify `backend/app/models.py`, `backend/app/api/schemas.py` (PageUpdate, PageOut, GenerationOut), `backend/app/api/pages.py` (PATCH), frontend `src/api/schema.d.ts`/`openapi.json`. Test: `tests/test_migrations.py`, `tests/test_api_pages.py`.

**Interfaces — Produces:** `Page.auto_cleanup: bool = False`, `Page.cleanup_prompt: str | None`, `Generation.source_generation_id: int | None` (FK generations.id, ON DELETE SET NULL, follow existing FK style); `PageUpdate.auto_cleanup: StrictBool | None`, `PageUpdate.cleanup_prompt: str | None` (≤600; "" → null); `PageOut` and `GenerationOut` expose them.

- [ ] Step 1 failing tests: migration upgrade/downgrade adds/removes the three columns; PATCH sets both fields; 601-char prompt → 422; PATCH with a different `scene_prompt` (alone or with other fields) sets `auto_cleanup=false`, `cleanup_prompt=null`; same `scene_prompt` text leaves them; PATCH that sets scene AND auto_cleanup=true in the same body → the explicit values win (pack apply sends them together).
- [ ] Step 2 run → fail. Step 3 implement. Step 4 full backend suite + vitest/tsc green. Step 5 commit `feat: pages can ask for background cleanup; generations record their source`.

### Task 2: `qwen_edit_cleanup` workflow and engine

**Files:** Create `comfy_workflows/qwen_edit_cleanup/workflow_api.json`, `manifest.yaml`; Modify `backend/app/services/engine.py` (GenerationRequest), `backend/app/services/comfy_engine.py`, `backend/app/services/gen_request.py` (`decode_request` loads the source image), `tests/fake_engine.py` (and the e2e fake backend if it has its own engine). Test: `tests/test_comfy_engine.py`, `tests/test_preflight.py` (or the workflow registry test), `tests/test_gen_request.py` if present.

**Interfaces — Produces:** `GenerationRequest.source_image: bytes | None = None`; `WORKFLOW_CLEANUP = "qwen_edit_cleanup"`; ComfyEngine uses it when `kind == BACKGROUND and source_image is not None`, uploading the image (suffix `"src"`) as manifest param `source_image`; `decode_request` reads `source_generation_id` from the request JSON and loads that generation's image from storage.

- [ ] Step 1: GET `http://127.0.0.1:8188/object_info/<Class>` for every node class in the new workflow (same classes as `qwen_edit_pose_cutout` minus the four BiRefNet/mask nodes) and record the inputs in the report.
- [ ] Step 2 failing tests: manifest loads, params = prompt/source_image/seed/width/height (+ fixed values as in the pose workflow), outputs = OUTPUT only, models = pose workflow's; mock-ComfyUI engine test: BACKGROUND + source_image → uploads `*_src.png`, builds `qwen_edit_cleanup`, returns OUTPUT; BACKGROUND without source → `qwen_background` unchanged; preflight lists the new workflow's models/nodes; fake engine returns a valid PNG for cleanup requests.
- [ ] Step 3 run → fail. Step 4 implement (workflow JSON derived from `qwen_edit_pose_cutout/workflow_api.json`: drop the 4 nodes, rename `CHARACTER_REF` title to `SOURCE_IMAGE`). Step 5 suite green. Step 6 commit `feat: qwen_edit_cleanup workflow — edit a background in place`.

### Task 3: cleanup job and the button endpoint

**Files:** Modify `backend/app/services/prompts.py` (GENERIC constant), `backend/app/services/jobs.py`, `backend/app/api/compose.py`, `backend/app/services/pipeline.py` (`on_generation_failed`), `backend/app/api/history.py` (`_failed_kinds`). Test: `tests/test_api_compose.py` (or new `tests/test_background_cleanup.py`), `tests/test_pipeline.py`, `tests/test_api_history.py`.

**Interfaces — Consumes:** Task 1 fields, Task 2 request field. **Produces:** `GENERIC_CLEANUP_PROMPT: str`; `cleanup_job(session, book, page, source: Generation) -> Job` (kind BACKGROUND, prompt = page.cleanup_prompt or GENERIC, width/height = source's, seed random, `source_generation_id` set, `user_text=""`); `is_cleanup(gen) -> bool` (source_generation_id is not None); `POST /api/pages/{page_id}/backgrounds/cleanup` (`?oneclick` like the background endpoint) → `GenerationOut`, 202.

- [ ] Step 1 failing tests: endpoint queues a cleanup of the page's selected background with GENERIC (or the page's prompt), consumes 1 count, take semantics like 「換一張」 (oneclick: snapshot + always replaces + recompose); no selected background → 409 (existing message); placement without `frame` → 409 「有框的背景（肖像模式）才能清」; quota exhausted → existing 429; other owner → 404; a failed cleanup generation does NOT set page attention and `_failed_kinds` ignores it (再試一次 does not redraw the background).
- [ ] Step 2 run → fail. Step 3 implement. Step 4 suite green. Step 5 commit `feat(api): 清掉框外雜物 — clean the selected background`.

### Task 4: automatic cleanup and quota

**Files:** Modify `backend/app/services/pipeline.py` (`on_generation_succeeded` / failed), `backend/app/services/jobs.py`, `backend/app/services/usage.py` (only if a helper is needed), `backend/app/api/produce.py` (`produce_plan`, `produce`), `backend/app/api/compose.py` (background endpoints on auto pages), `backend/app/services/worker.py` if the success hook lives there. Test: `tests/test_pipeline.py`, `tests/test_api_produce.py`, new `tests/test_auto_cleanup.py`.

**Interfaces — Consumes:** Task 3 `cleanup_job`, `is_cleanup`. **Produces:** request flag `cleanup_reserved: true` on a background generation whose cleanup count is already reserved (usage extra carried over to the cleanup so refunds work).

- [ ] Step 1 failing tests:
  - AI background (not cleanup, not upload) succeeds on an `auto_cleanup` page → not selected; one cleanup queued with the page's prompt/GENERIC, carrying the reserved count (no new reserve); its take/review flags copied from the source.
  - That cleanup succeeds → selected with the same rules a normal background success uses, page pipeline queued; it does not queue another cleanup.
  - Cleanup fails → source background selected, pipeline queued, no attention.
  - Background fails on an auto page → 2 counts refunded.
  - No reserved count and quota exhausted at trigger time → no cleanup, source selected, pipeline queued.
  - `produce_plan`: auto page backgrounds count 2 in `total`/`fits`/`estimate_s`; `produce` reserves accordingly and defers pages that don't fit, never splitting a background from its cleanup.
  - 「換一張」/oneclick 「AI 再畫一張」 on an auto page reserves 2; only 1 left → 429, nothing queued.
  - Non-auto pages: identical behaviour and counts to today (assert existing tests unchanged + one explicit test).
  - Uploaded background on an auto page → no cleanup.
- [ ] Step 2 run → fail. Step 3 implement. Step 4 full backend suite green (incl. `test_pipeline_byte_identity.py`). Step 5 commit `feat(backend): 華麗相框 pages clean their backgrounds automatically`.

### Task 5: frontend — buttons, badge, pack data

**Files:** Modify `frontend/src/api/client.ts` (+ hook in `api/hooks.ts`), `frontend/src/features/editor/ComposeWorkspace.tsx`, `frontend/src/features/fix/rows/BackgroundRow.tsx` (+ `useFixActions.ts` if actions live there), thumbnail component(s) that list background generations, `frontend/src/lib/themePacks.ts`, `frontend/src/lib/applyImport.ts`, `frontend/src/features/setup/SetupPage.tsx`. Test: matching `*.test.tsx`, `themePacks.test.ts`, `applyImport` test.

**Interfaces — Consumes:** endpoint from Task 3; `PageOut.auto_cleanup/cleanup_prompt`, `GenerationOut.source_generation_id`, `Placement.frame`. **Produces:** `cleanupBackground(pageId: number, oneclick?: boolean): Promise<Generation>`; `ThemePack.cleanups?: { auto: boolean; prompt?: string }[]`.

- [ ] Step 1 failing tests: both cards show 「清掉框外雜物」; disabled when no selected background, background busy, or placement has no frame (with the hint 「有框的背景（肖像模式）才能清」 shown in the not-portrait case); click calls the client (oneclick in BackgroundRow) and shows the busy state; thumbnails of generations with `source_generation_id` show 「清過」; applying a pack with `cleanups` PATCHes `auto_cleanup`/`cleanup_prompt` per page, only when the text is unchanged (same rule as lineDetail/placements); 華麗相框 has 12 `cleanups`, all `auto: true`, page 4 `prompt` = CLOVER verbatim, others without prompt.
- [ ] Step 2 run → fail. Step 3 implement. Step 4 vitest/tsc/eslint green. Step 5 commit `feat(web): 清掉框外雜物 button; 華麗相框 cleans automatically`.

### Task 6: GPU acceptance and walkthrough (controller)

- [ ] Step 1: GPU idle; message roboskin-38 start/end (~15 min); restart nothing of the user's dev.sh.
- [ ] Step 2: acceptance servers (8001/3001, scratch packstore, SwiftShader Playwright); one-click 華麗相框 book with the snow girl; contact sheet + the 12 raw/cleaned backgrounds side by side into `experiments/results/background_cleanup_20261124/`.
- [ ] Step 3: walkthrough screenshots: editor background card button on a portrait page → cleaned thumbnail with 「清過」; a non-portrait page shows the disabled button + hint; one-click page button + ↶ 復原.
- [ ] Step 4: fixes (tests first) if needed; Step 5: report to the user and stop for review (merge only after the user's OK).
