# One-Click Book Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** From a filled 「做一本新的著色書」 page, one button produces the whole book. A server-side pipeline composes, renders line art, auto-fixes and marks each page print-ready. B shows the whole book, C fixes one page with undo, and the app gets the 童趣繪本 look.

**Architecture:**
- Backend:
  - new page fields and three tables (`page_task`, `page_history`, plus split stroke layers);
  - a pipeline service run by the existing single worker;
  - `/produce` and `/produce/plan`;
  - undo, restore and character upload or change endpoints.
- Frontend:
  - new routes `/books/new`, `/books/{id}/setup` (A), `/books/{id}` (B) and `/books/{id}/page/{pageId}` (C);
  - new design tokens;
  - the old editor kept at `/books/{id}/editor`.

**Tech Stack:** FastAPI, SQLModel/SQLite with Alembic, Pillow, Next.js 16, React 19, TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-11-oneclick-book-design.md`. It is binding. Mockups are in `docs/superpowers/specs/mockups/`; where they mention the 動作包, the spec text wins.

## Global Constraints
- **Where to work.** Worktree `/ai-data/repos/coloring-books-phase13`, branch `phase13-oneclick`.
  - Never touch `/ai-data/repos/coloring-books`.
  - Never read `scripts/dev.env`.
  - Ports 3000, 8000 and 8188 are off-limits. Playwright uses 8010 and 3010.
- **Tests.**
  - Backend: `env -u PYTHONPATH PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`.
  - In frontend/: `npx vitest run`, `npm run lint`, `npx tsc --noEmit`, `PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e`. Run `npm ci` once if `node_modules` is missing.
  - Regenerate API types with `npm run gen:api` after any backend schema change.
- **Copy and style.** zh-Hant copy exactly as the spec gives it. The palette, borders, shadows and fonts are exactly as in spec §2.
- **Ownership.** Every new route uses the `owned_*` guards, and returns 404 for foreign or unknown ids. Add each new route to `tests/test_ownership.py` explicitly.
- **User choices win.** The pipeline never overwrites a user-chosen background, pose, upload, placement or caption.
- **Commits.** Local only, never push. Every commit message ends with:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`

## Review Focus
1. Pressing 開始做這本書 twice, or while jobs are queued, never duplicates jobs or exceeds the cap. A partial run defers the rest. (Task 3)
2. The pipeline never overwrites a user choice, and a failure always surfaces as `attention="failed"` with no stuck 製作中. This includes recovery after a restart. (Task 2)
3. Undo restores the user's pen strokes after a re-compose cleared them, and redo after undo works. (Task 4)
4. Existing books, with old PASSED or DRAFT pages and legacy whole-page generations, show the right B states, and nothing changes them until 「繼續做」. (Task 3)
5. At 390 px, A, B and C have no horizontal overflow, and every control is at least 44 px. (Tasks 6–8)

---

### Task 1: Data model and migration 0011
- **Files:**
  - `backend/migrations/versions/0011_oneclick.py`
  - `backend/app/models.py`
  - `tests/test_migrations.py`
  - the line-edit storage (`backend/app/services/page_lineart.py` and `models.py:178` edits rows)
- **Changes:**
  - Add to page: `attention: str | None`, `attention_detail: str | None`, `auto_background_generation_id: int | None`, `auto_pose_generation_id: int | None`.
  - New table `page_task(id, page_id FK, kind str, status str, error str|None, created_at, updated_at)`.
  - New table `page_history(id, page_id FK, seq int, snapshot_json str, created_at)`.
  - Split the stroke layers: line-edit rows gain `layer: "auto" | "user"`. Existing rows become `user`. Print and render use both.
- [ ] **Step 1: write failing tests.**
  - Migration upgrade and downgrade.
  - The model fields exist.
  - Strokes are stored per layer, and rendering includes both.
  - The existing line-edit API writes `user`; autofix writes `auto`.
- [ ] **Step 2:** confirm the tests fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the full backend suite.
- [ ] **Step 5:** commit `feat: one-click data model (attention, auto choices, page tasks, history, stroke layers)`.

### Task 2: Page pipeline and worker integration
- **Files:**
  - `backend/app/services/pipeline.py` (new)
  - `backend/app/services/worker.py`
  - `backend/app/api/compose.py` and `lineart.py` (call the shared functions; no duplication)
  - tests
- **Interfaces:**
  - `enqueue_page_task(session, page_id, kind="pipeline")` is idempotent: at most one QUEUED task per page.
  - `run_page_pipeline(session, storage, page_id)` runs auto-select → compose → lineart → autofix (auto layer) → PASSED. It records `auto_*` and clears `attention`.
  - On any exception it sets `attention="failed"` and `attention_detail` (zh), and marks the task FAILED.
- **Triggers:**
  - a page-scoped background or pose generation succeeds;
  - a page-scoped background or pose generation fails, which sets `attention="failed"` with the generation's zh error;
  - an API change on C (Task 4 calls `enqueue_page_task`).
- **Worker:** after each generation (and when idle), drain QUEUED `page_task` rows. On startup, re-queue RUNNING tasks.
- [ ] **Step 1: write failing tests.**
  - The full happy path with a fake engine.
  - Auto-select respects a user-selected background or pose.
  - Placement is saved or defaulted.
  - A failure surfaces as `attention`.
  - Idempotent enqueue.
  - Restart recovery.
  - Composites are reused when nothing changed. The existing compose dedupe behaviour is kept.
- [ ] **Step 2:** confirm the tests fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the full suite.
- [ ] **Step 5:** commit `feat: server-side page pipeline`.

### Task 3: `/produce`, `/produce/plan` and the book overview API
- **Files:** `backend/app/api/produce.py` (new), schemas, usage, tests.
- **Interfaces:**
  - `GET /api/books/{id}/produce/plan` returns `{backgrounds, poses, total, estimate_s, remaining, fits}`. `remaining` is null for an uncapped admin.
  - `POST /api/books/{id}/produce` returns `{queued:[ids], deferred_pages:[page ids]}`, in page order, background before pose.
    - It reserves only what fits.
    - It skips kinds that already have a QUEUED or RUNNING job.
    - It returns 429 「今天的生成次數已用完，明天再來」 when 0 remain.
    - It also enqueues pipeline tasks for pages that already have both images but no composite.
  - `GET /api/books/{id}/overview` returns:
    - per page, `{id, position, state: "ready"|"attention"|"working"|"todo", gap_count, caption_zh, caption_en, thumb_url, attention_detail}`;
    - the run progress, `{queued, running, done}`, and the deferred count.
    - Existing books map PASSED to ready, an active job to working, attention to attention, and anything else to todo.
- [ ] **Step 1: write failing tests.**
  - Plan counts, covering AI-mode pages versus uploaded backgrounds.
  - A partial run defers the right pages.
  - Pressing twice adds no duplicates.
  - 429 at 0.
  - Admin uncapped.
  - Overview states for a new book and for an existing legacy book.
  - Ownership 404.
- [ ] **Step 2:** confirm the tests fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the full suite.
- [ ] **Step 5:** commit `feat: one-click produce and book overview API`.

### Task 4: Undo, restore, character upload and change
- **Files:** `backend/app/api/history.py` (new), `books.py` (character), `compose.py` (C mutations push snapshots), tests.
- **Interfaces:**
  - `push_snapshot(session, page)` is called before every C mutation. The C mutations are:
    - select a background or pose;
    - upload a background;
    - regenerate;
    - placement;
    - action or caption edit;
    - user strokes;
    - restore.
  - `POST /api/pages/{id}/undo` and `POST /api/pages/{id}/redo` return `PageOut` plus `{can_undo, can_redo}`.
    - Applying a snapshot re-selects the images and placement and restores the user strokes and text.
    - If the images or placement changed, it enqueues the pipeline and restores the user strokes once the new composite exists.
  - `POST /api/pages/{id}/restore {item: "background"|"pose"|"lines"}` restores from `auto_*`. For `lines` it clears the user strokes.
  - Re-compose clears the user strokes (they are saved in the snapshot first).
  - At most 50 snapshots per page.
  - `POST /api/books/{id}/character/upload` (multipart) runs the same validation as the background upload. It letterboxes the image on white at the character size, creates a CHARACTER generation with `uploaded=true`, and selects it.
  - `POST /books/{id}/character` may now change an existing character when given `confirm_change: true`; without it, it still returns 409. Existing poses stay.
- [ ] **Step 1: write failing tests.**
  - An undo/redo sequence across select, placement, strokes and a re-compose.
  - The history cap.
  - Restore for each item.
  - Upload validation and letterboxing.
  - A character change with and without confirmation.
  - Ownership 404.
- [ ] **Step 2:** confirm the tests fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the full suite.
- [ ] **Step 5:** commit `feat: page undo/restore and character upload/change`.

### Task 5: 童趣繪本 design tokens and shared UI
- **Files:** `frontend/src/app/globals.css`, the shared UI components (Button, Dialog, Toast, chips, header shell) and their tests, `designTokens.test.ts`.
- **Changes:**
  - Replace the tokens with the spec §2 palette: ink borders, pill buttons, hard shadows, dotted ground, 900-weight headings.
  - Keep the token names where possible, so the existing modules pick up the change.
  - Contrast: assert AA in `designTokens.test.ts`, for ink on every fill and white on ink.
- [ ] Write failing token tests → implement → run vitest, lint, tsc and e2e (expect visual-only changes) → commit `style: 童趣繪本 tokens and shared components`.

### Task 6: B 整本書, plus the export sheet
- **Files:** `frontend/src/app/books/[bookId]/page.tsx` (B), `frontend/src/features/book/*` (new), the export sheet (reuse the export drawer logic), routes.
- **Behaviour:** as spec §4.3 and `mockups/2_book.png`. Polls `/overview`. Shows a toast and changes the tab title when finished. 「繼續做」 calls `/produce`. 「印出這本書」 opens the thumbnail glance, then the PDF options.
- [ ] Write failing vitest tests → implement → vitest, lint, tsc → commit `feat: 整本書 overview and export glance`.

### Task 7: C 處理一頁
- **Files:** `frontend/src/app/books/[bookId]/page/[pageId]/page.tsx`, `frontend/src/features/fix/*` (new), reusing LineFixCanvas, the 表情 and 面向 chips, and the upload.
- **Behaviour:** as spec §4.4 and `mockups/3_fix.png`. 復原 and 重做 are enabled from `{can_undo, can_redo}`. The item rows show 「↺ 還原成自動畫的那張」 when the choice differs from `auto_*`. Dragging saves the placement and the page re-runs. 這頁可以印了 → next page. The page has a 「進階編輯」 link.
- [ ] Write failing vitest tests → implement → vitest, lint, tsc → commit `feat: 處理一頁 with undo and restore`.

### Task 8: A 做一本新的著色書 / setup
- **Files:** `frontend/src/app/books/new/page.tsx`, `frontend/src/app/books/[bookId]/setup/page.tsx`, `frontend/src/features/setup/*` (new). It reuses the story parser, the theme pack, the AI prompt, character generation and the upload.
- **Behaviour:** as spec §4.5 and `mockups/1_create.png`.
  - The book is created once section 1 is valid.
  - Character: generate, 再畫一隻, choose, upload. Changing the character asks for confirmation.
  - Story preview with per-page `AI 畫背景 / ↑ 上傳背景`.
  - A bottom bar driven by `/produce/plan`.
  - 開始做這本書 does the import, then the uploads, then `/produce`, then goes to B.
  - The old `/books/{id}/character` and `/story` routes redirect to setup. On the home page, 「建立新繪本」 goes to `/books/new`.
- [ ] Write failing vitest tests → implement → vitest, lint, tsc → commit `feat: 做一本新的著色書 setup page`.

### Task 9: GC, docs and the end-to-end flow
- **Files:** `scripts/storage_gc.py` and its tests, README, `frontend/e2e/oneclick.spec.ts`.
- **GC:** protect `auto_*`, the `page_history` snapshot references and active `page_task` rows.
- **README:** the new flow.
- **e2e:** spec §5. Also: 390 px with no overflow on A, B and C, and the existing e2e updated for the new routes.
- [ ] Write failing tests → implement → all five suites → commit `feat: one-click e2e, GC protection, docs`.

### Task 10: Real-GPU acceptance (the controller runs this; ask the user first)
- [ ] The vehicles theme pack with the bunny, one click from an empty book to a PDF. Record the total time and any attention pages.
- [ ] One uploaded hand-drawn-style character gives usable poses.
- [ ] Write `experiments/results/phase13_oneclick_acceptance_<ts>/NOTES.md`, run `POST /free`, and notify the user.
