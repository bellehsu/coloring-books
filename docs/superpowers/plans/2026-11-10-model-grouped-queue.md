# Model-grouped queue and batch backgrounds: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- When several jobs are queued, the worker runs jobs for the same model family (SDXL / Qwen) first, and the queue position matches the actual run order.
- Add 「產生所有背景」 to queue a background for every eligible page in one click.

**Architecture:**
- A new pure function `plan_order` (`backend/app/services/scheduling.py`) defines the run order. `GenerationWorker._claim` and `queue_position` both use it.
- New batch endpoint `POST /books/{id}/backgrounds/batch`.
- The frontend Filmstrip gets a button with a confirmation dialog.

**Tech Stack:** FastAPI, SQLModel, SQLite, Next.js 16 + React 19 + TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-10-model-grouped-queue-design.md`

## Global Constraints

- Family values are `"qwen"` and `"sdxl"`. `qwen` means kind POSE whose request_json has `pose_mode == "precise"`; everything else is `sdxl`. A request_json that can't be parsed counts as `sdxl`.
- `queue.max_wait_s` defaults to 180 (>= 0), overridable with `APP_QUEUE__MAX_WAIT_S`. Add it to `config/settings.example.yaml` as well.
- Skip reasons (zh-Hant, verbatim): 「沒有場景描述」「已有背景」「背景產生中」.
- UI copy (verbatim):
  - Button: 「產生所有背景」.
  - Confirmation: 「會為 N 頁產生背景」, plus, when pages were skipped, 「（M 頁略過：第 x 頁沒有場景描述、…）」.
  - Background-tab hint: 「先寫好每一頁的場景描述，再按頁面清單上方的『產生所有背景』，可以減少等待」.
- Single worker; never run jobs concurrently. Never touch `storage/app.db`. Commit locally only, with the trailer:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`.
- Test commands:
  - Backend: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`.
  - Frontend: `cd frontend && npm test && npm run lint && npx tsc --noEmit`.
  - e2e: run in the scratch copy. Same approach as Phase 6: rsync to `<scratchpad>/e2e-copy2/` and hard-link node_modules.

## Review Focus

1. **The displayed queue position must equal the actual run order:** "position 1" must be the next job to run. Test: Task 1 asserts that the order produced by repeated `_claim` equals the order from `queue_position`.
2. **Anti-starvation:** a job that has waited past `max_wait_s` goes first, even if it is in the other family. Test: Task 1.
3. **Pressing 「產生所有背景」 twice in a row** must not queue anything twice. The second call skips those pages with 「背景產生中」. Test: Task 2.
4. **A page with a background in its history that isn't selected** is skipped with 「已有背景」. A page whose only background was discarded is not skipped. Test: Task 2.
5. **Worker restart:** with no last family, the order falls back to oldest first, and RUNNING rows that get re-queued still sort correctly. Test: Task 1.

---

### Task 1: scheduling order

**Files:**
- Create: `backend/app/services/scheduling.py`
- Modify: `backend/app/services/worker.py` (`_claim`, `queue_position`, record the family of the last claimed job), `backend/app/config.py` (`QueueSettings.max_wait_s`), `backend/app/main.py` (pass it to the worker), `config/settings.example.yaml`
- Test: `tests/test_scheduling.py`, `tests/test_worker.py`

**Interfaces:**
- Produces:
  - `family_of(kind: GenKind, request_json: str) -> Literal["qwen","sdxl"]`
  - `@dataclass QueuedJob(id: int, family: str, created_at: datetime)`
  - `plan_order(queued: list[QueuedJob], last_family: str | None, now: datetime, max_wait_s: float) -> list[int]`
  - `GenerationWorker(..., max_wait_s: float = 180.0)`, with the attribute `last_family: str | None`.
- Rules for `plan_order`:
  1. If any job has `now - created_at > max_wait_s`, the overdue jobs go first, oldest first.
  2. Then same-family jobs, oldest first.
  3. Then the rest, oldest first.
  If `last_family` is None, everything is oldest first, apart from the overdue jobs.
- Order the queued rows by `created_at`, then by `id`.

- [ ] **Step 1: Write failing tests.**
  - `test_family_of`: precise POSE → qwen; fast POSE, missing key, BACKGROUND and broken JSON → sdxl.
  - `test_plan_order_groups_same_family`: last=qwen; queued [bg1, pose2(qwen), bg3, pose4(qwen)] → [2, 4, 1, 3].
  - `test_plan_order_no_last_family_is_fifo`.
  - `test_plan_order_overdue_first`: bg1 has waited 200 s while last=qwen → bg1 goes first.
  - Worker: `test_claim_order_matches_queue_position`. Queue a mix of families and set `last_family`. Read `queue_position` for every id, then `_claim` and finish them one by one. The claim order must equal the order of the positions, and the job with position 1 (or 0 when nothing is running) must be claimed first.
  - Worker: `test_last_family_updates_on_claim`.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement. Keep the atomic conditional UPDATE in `_claim`, applied to the first id from `plan_order`; if rowcount is 0, retry. `queue_position`: RUNNING → 0; QUEUED → its index in `plan_order` + 1 if something is running, otherwise its index.
- [ ] **Step 4:** The whole suite passes.
- [ ] **Step 5:** Commit `feat(worker): group queued jobs by model family`.

### Task 2: batch background endpoint

**Files:**
- Modify: `backend/app/api/compose.py` (reuse the single-background generation logic), `backend/app/api/schemas.py` (`BatchBackgroundsOut`, `BatchSkip`)
- Regenerate: `frontend/src/api/openapi.json`, `schema.d.ts`
- Test: the existing compose API test file

**Interfaces:**
- Produces: `POST /api/books/{book_id}/backgrounds/batch` → 202 `{queued: GenerationOut[], skipped: [{page_id: int, position: int, reason: str}]}`. A missing book returns 404.
- Pages are visited in `position` order. Each is skipped with the first reason that applies:
  1. `scene_prompt.strip() == ""` → 「沒有場景描述」;
  2. `background_generation_id` is set, or the page has a BACKGROUND that is SUCCEEDED and not discarded → 「已有背景」;
  3. there is a BACKGROUND in QUEUED/RUNNING → 「背景產生中」.
- Queuing reuses the single-background request building (sketch snapshot, controlnet strength). Extract a helper rather than copying it.

- [ ] **Step 1: Write failing tests.**
  - The three skip reasons each fire correctly.
  - `queued` follows position order.
  - Calling twice in a row: the second call has `queued` empty and all those pages show 「背景產生中」.
  - A page whose only background was discarded is queued.
  - A page with a sketch: its request_json has a `structure_image` snapshot.
  - Book not found → 404.
- [ ] **Step 2:** Run and confirm they fail.
- [ ] **Step 3:** Implement, then run `PYTHON=... npm run gen:api` in `frontend/`.
- [ ] **Step 4:** Backend suite and `tsc` pass.
- [ ] **Step 5:** Commit `feat(api): batch background generation` (types in the same commit).

### Task 3: frontend button, hint, e2e, docs

**Files:**
- Modify:
  - `frontend/src/api/hooks.ts` (`useBatchBackgrounds(bookId)`; on success invalidate jobs and every page's generations)
  - `frontend/src/features/editor/Filmstrip.tsx` (+ css)
  - `frontend/src/features/editor/EditorPage.tsx` (wiring)
  - `frontend/src/features/editor/BackgroundPanel.tsx` (hint)
  - `frontend/e2e/flow.spec.ts` or a new `frontend/e2e/batch.spec.ts`
  - `README.md` (queue behaviour, batch endpoint, usage hint)
- Test: `Filmstrip.test.tsx`, `EditorPage.test.tsx`, `BackgroundPanel.test.tsx`

**Interfaces:**
- Consumes: the Task 2 endpoint.
- Count N on the frontend, using the same three conditions from the page list and generation data the editor already has, for the button state and the confirmation. The backend result is what actually counts. After it finishes, show a toast 「已排入 N 頁背景」, plus 「、略過 M 頁」 when pages were skipped.

- [ ] **Step 1: Write failing tests.**
  - When N = 0 the button is disabled, with `title` 「沒有需要產生背景的頁面」.
  - Clicking opens a confirmation dialog containing 「會為 2 頁產生背景」 and the skip list.
  - Confirming calls `POST /books/{id}/backgrounds/batch` and shows the toast.
  - Cancelling sends nothing.
  - The BackgroundPanel shows the hint text.
- [ ] **Step 2:** Run and confirm they fail.
- [ ] **Step 3:** Implement, using the existing dialog/toast components and tokens.
- [ ] **Step 4:** e2e: create a book with 3 pages via the API seed (2 with scenes, 1 without). Click 「產生所有背景」 → confirm → the two pages each get one background card, and the third shows no new card. Run all e2e in the scratch copy; everything must pass.
- [ ] **Step 5:** README.
- [ ] **Step 6:** Commit `feat(editor): generate all backgrounds` and `docs: batch backgrounds and grouped queue`.

### Task 4: real-GPU acceptance (controller runs this)

- [ ] Same setup as Phase 6 Task 7: separate backend and frontend on 8020/3020 with scratch storage.
  - Create a 3-page book and write all three scenes first.
  - 產生所有背景 → wait for completion → generate each page's precise pose in turn.
  - Then compose → line art → QA → pass.
  - Record each step's time and the total, and compare with phase6 (3 pages × ~75 s).
  - Record the result in `experiments/results/phase7_grouped_queue_ui_check_<ts>/NOTES.md` and commit.

---

## Self-Review record

- Spec coverage:
  - §3.1 family → Task 1 `family_of`;
  - §3.2 order, anti-starvation and queue position → Task 1;
  - §3.3 endpoint → Task 2; UI and hint → Task 3;
  - §4 settings → Task 1;
  - §5 success criteria → Tasks 1–4.
- Names are consistent throughout: `plan_order`, `family_of`, `QueuedJob`, `max_wait_s`, `last_family`, `useBatchBackgrounds`, `BatchBackgroundsOut`.
