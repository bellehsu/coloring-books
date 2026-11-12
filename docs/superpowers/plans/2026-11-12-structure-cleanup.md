# Structure Cleanup + Caption Parsing + e2e Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the deferred structural tasks from the 2026-11-12 code review. Fix the one-line 中文／英文 caption parsing bug found in the real-GPU walkthrough, and make the e2e suite stable under a full run.

**Architecture:** Two small behaviour fixes come first, each test-first:
- Task 1: the story parser;
- Task 2: e2e isolation.

Then come the four structural tasks deferred from `docs/superpowers/plans/2026-11-12-code-review-fixes.md` (Tasks 14–17 there, renumbered 3–6 here). These are pure moves and splits with no behaviour change. Task 7 is the controller's real-GPU browser walkthrough.

**Tech Stack:** FastAPI, SQLModel/SQLite, pytest; Next.js 16, React 19, TanStack Query, Vitest, Playwright.

**Spec:**
- review reports `docs/reviews/2026-11-12/{A,B,C,D,E}-*.md` (finding IDs);
- `experiments/results/review_fixes_acceptance_20261112_1624/NOTES.md` (the caption bug);
- the original plan `docs/superpowers/plans/2026-11-12-code-review-fixes.md` (Global Constraints, which still apply).

## Global Constraints

- Project rules are in `CLAUDE.md` (zh-Hant). UI copy is in Traditional Chinese.
- Branch `structure-cleanup` in the main checkout `/ai-data/repos/coloring-books`. Local commits only, no worktrees, and dev.sh stays stopped.
- Every commit ends with a `Co-Authored-By: Claude <model> 5.5 <noreply@anthropic.com>` line naming the authoring model, plus `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`.
- Never touch `storage/`, `storage-r2/`, `storage-backup-*`, `scripts/dev.env` or ComfyUI (8188) in Tasks 1–6.
- Backend tests: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`.
- Frontend: `cd frontend && npm test && npm run lint && npx tsc --noEmit`.
- e2e: `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e`.
- Regenerate API types after any API shape change (`npm run gen:api`).
- CSS modules use design tokens only.
- Structural tasks (3–6) must not change behaviour. Their only test edits are import paths and renames. Any behaviour change found necessary is reported as a concern, not made.
- User decisions stay as they are: A3 unchanged; E2 auto-swap is undoable; C1/C3/C4 are out of scope.

## Review Focus

1. **Stories pasted from ChatGPT or Claude** using 「中文：…　英文：…」, 「中文：… / 英文：…」, 「中文：…｜英文：…」 or 「英文：… 中文：…」 on one line must split correctly. Task 1 pins every separator.
2. **A Chinese caption that itself contains the word 英文** (e.g. 「中文：學英文」) with no 英文 label after it must stay whole. Task 1 pins it.
3. **Running the full e2e suite twice in a row** must pass both times with `workers: 1` and no retries. Task 2 verifies this.
4. **FixPage after the split:** every keyboard and save path from Task 9 still works. The existing FixPage tests must pass unchanged apart from import paths (Task 6).
5. **Moving modules must not create import cycles.** Lazy imports used to dodge cycles must be removed, not added (Task 3).

---

### Task 1: One-line 中文／英文 captions in pasted stories

**Files:** `frontend/src/lib/storyImport.ts`, `frontend/src/features/setup/StorySection.tsx`. Tests in `frontend/src/lib/storyImport.test.ts` and `frontend/src/features/setup/StorySection.test.tsx` (or the existing setup test that renders it).

**Decisions:**
- **Splitting a 中文 line:** when a 中文 line's value contains an English label (`英文` or `English`, case-insensitive, followed by `:` or `：`), split it there.
  - The text before the label is the Chinese caption.
  - Before trimming, strip trailing separators from it: whitespace including the full-width space U+3000, `/`, `／`, `|`, `｜`, `、`, `，` and `,`.
  - The text after the label is the English caption.
- **Splitting an 英文 line:** symmetric. When the line contains a 中文 label after the value, split the same way.
- **When not to split:** a 中文 value containing 英文 without a following colon (e.g. 「學英文」) is not split.
- **Preview count:** the 「讀到 N 段」 line adds 「，M 段不會匯入」 when there are problems (M = number of problem paragraphs), so a skipped page is visible next to the count.

- [ ] Write failing tests first:
  - `parseStory` with each separator (full-width space, half-width space, ` / `, `／`, `｜`, `|`, `，`): 「中文：公園　英文：Park」 → `captionZh "公園"`, `captionEn "Park"`;
  - 「英文：Park 中文：公園」 → the same;
  - 「中文：學英文」 → `captionZh "學英文"`, no `captionEn`;
  - the walkthrough's exact 3-page story → 3 pages, and page 3 gets `captionZh "麵包店"`, `captionEn "Bakery"` with no problems;
  - StorySection renders 「讀到 3 段，1 段不會匯入」 when one paragraph has a problem.
- [ ] Implement, run the frontend checks, commit `fix(story): split one-line 中文／英文 captions; show how many paragraphs won't be imported`.

### Task 2: e2e stability under a full run

**Files:** `frontend/e2e/*.spec.ts`, `frontend/e2e/seed.ts`, `frontend/playwright.config.ts`, and if needed `scripts/dev_fake_backend.py` (test-only knobs).

**Decision:** the full run flakes because every spec shares one fake backend with one global generation queue, so jobs queued by earlier specs delay later ones. The specs that flaked are help-overlay, oneclick:203, sharing and setup-back.
- Each spec that queues generations must leave the queue empty in `afterAll`. Add a seed helper `drainQueue(request)` that cancels or waits for queued and running jobs via the existing API: discard or cancel queued generations, or poll `/api/system/queue` until idle, up to 60 s.
- Specs must wait on the specific response or state they need, never fixed sleeps. help-overlay must not depend on queue state at all.
- Keep `workers: 1` and `retries: 0` locally. Do not paper over the flakes with retries.

- [ ] Reproduce first: run the full e2e suite twice and record which specs fail. Then fix them, and run the full suite twice more; both runs must pass, with output in the report.
- [ ] Commit `test(e2e): isolate specs from the shared generation queue (no more load flakes)`.

### Task 3: Backend services split (B13, B15, B16, A13, A14)

**State since the review-fixes merge (9bd5505):** `engine_requeues` and `engine_unavailable_since` are protected request keys in `worker._CORE_KEYS`. They move to `services/gen_request.py` together with `take` and `usage_*`. `backend/app/api/guards.py` imports `MAX_BG_UPLOAD_BYTES` from `api/compose.py`, so update that import when the constant is renamed.

**Files:**
- Create `backend/app/services/jobs.py`, holding `_Job`, `_add_queued`, `_enqueue_jobs`, `_BookState`, `_background_job`, `_pose_job`, `_take`, `_BATCH_LOCK`, `has_character` and `build_job` (rename the private ones to public names without the leading `_`).
- Create `backend/app/services/uploads.py`, merging `read_upload`, `_mark_stored` and `upload_bg.py`'s `letterbox`; delete `upload_bg.py`.
- Create `backend/app/services/gen_request.py`, holding `encode_request`/`decode_request`/`_is_retired` and the protected keys `take`, `usage_*` and `engine_requeues`, with a corrected docstring.
- Create `backend/app/services/slots.py`, holding `SLOTS`, `UPLOAD_ENGINE` and `newest_usable_ai(session, page_id, kind) -> int | None`, used by both `auto_select` and `auto_target`.
- Move `busy_page_ids` and `page_state` from `api/produce.py` to `services/page_state.py`.

**Decisions:**
- Renames:
  - `MAX_BG_UPLOAD_BYTES` → `MAX_UPLOAD_BYTES`;
  - `REDIRECT_CACHE_CONTROL` → `SIGNED_CACHE_CONTROL`.
- **B15** dedupes:
  - `GenerationOut.for_worker(gen, worker)`;
  - `last_variations(session, *where, n)`;
  - `parse_db_id()` in deps, used by shared.
- Route modules keep only HTTP concerns. Remove the lazy imports that existed to break cycles.

- [ ] Move. The full backend suite must pass with only import-path edits in tests. Grep that `from app.api.compose import _` no longer appears anywhere. Commit `refactor(backend): job queueing, uploads, request encoding and slots move to services (B13–B16, A13, A14)`.

### Task 4: Backend schemas, names, helpers and pipeline internals (B14, C7, C8, C10, C11, C2)

**Decisions:**
- **B14:**
  - Request and response models from the route modules (`generations.py`: GenerationUpdate, PendingJob, RecentJob, QueueStatus; `review.py`: CharacterCheckIn, PassIn) move into `api/schemas.py`.
  - URL builders and legacy inference move to `api/urls.py`.
- **C10:** create `backend/app/services/imageio.py` with:
  - `flatten_on_white(img) -> Image`, using rounding the way `lineart_extract._rgb` does;
  - `ink_to_png(ink: np.ndarray) -> bytes` and `png_to_ink(data: bytes) -> np.ndarray`.

  All copies switch to them. `compose()` calls `cutout()`. Pin the result with a test proving that line art for the fixture images is byte-identical before and after. Record the hashes in the test from the pre-change code.
- **C11** renames:
  - `export.compose_page` → `layout_print_page`;
  - `api/export.composed_page` → `print_page_image`;
  - `page_compose.load_image` → `get_pil_image`;
  - `_disk(radius)` → `disk_kernel(diameter)`;
  - `composite_matches` moves to `page_compose`.
- **C7:** `render_base` uses its own constants module `lineart_params.py`; all those values go into `_digest`. QA's `min_region_area` moves into `QAThresholds`. `RENDER_VERSION` is unchanged **only if** the line art stays byte-identical; the same pin test as C10 proves it.
- **C8:** add `render_ink(gray, content_px, line_width) -> np.ndarray` without metrics; the product path uses it. Experiment flags stay in `lineart_post` for `experiments/`.
- **C2:**
  - Move `luma`/`color_edges`/`hybrid`/`none` into `experiments/lineart_methods.py`, with `experiments/run_matrix.py` importing them.
  - The product `lineart_extract` keeps `_rgb`, `_xdog` and `extract_xdog(gray_or_rgb, sigma)`.
  - Its docstring describes xdog only.

- [ ] Do each item, run the full suite after each. Commit per item group: `refactor(backend): …`.

### Task 5: Backend performance internals (A15, A16, A17)

**Decisions:**
- **A15:** `worker.queue_positions(ids: list[int]) -> dict[int, int | None]` computes the plan once. All list endpoints use it.
- **A16:**
  - The overview queries only tasks with active status, or with `created_at >= book.produce_started_at`.
  - `finish_page_task` deletes that page's finished tasks beyond the newest 5.
- **A17:** `_finish`, `_fail`, `_after_success`, `_claim` and `largest_component` run through `asyncio.to_thread`.

- [ ] Write tests:
  - `test_queue_positions_batch_matches_single`;
  - `test_overview_ignores_old_finished_tasks`;
  - `test_finished_tasks_pruned_to_five`;
  - existing worker tests still pass.
- [ ] Commit `perf(backend): batch queue positions, prune page tasks, worker DB work off the event loop (A15–A17)`.

### Task 6: Frontend structure (E6, D5, E16, D11, D12, D13, E14, E15)

**State since the review-fixes merge (9bd5505):**
- FixPage has grown: Task 9 added `usePlacementSaves`, `pendingSaves`/`textSaves`, the QAError component and `recomposeRunning` usage. The split must keep all of it.
- `editsQueue.ts` has `flushAllEditsFor(qc, pageId, genId)`.
- `api/hooks.ts` has `recomposeRunning(page)`.

Also fold in these deferred minors, all pure moves or removals with no behaviour change:
- delete ExportOptions' unused `variant="editor"` branch and its tests;
- move `recomposeRunning` out of `api/hooks.ts` into `lib/pageState.ts`, because `lib/checklist.ts` should not import the hooks module;
- add an `editsBusyFor(qc, pageId, genId)` helper next to `flushAllEditsFor` and use it in EditorPage instead of the inline double peek;
- give the pages query one shared `pagesQueryOptions(bookId)` used by `usePages` and the editor's observer;
- render `QAError` with `role="alert"` only once (in LinesRow; DrawStage uses a plain `<p>`).

**Decisions:**
- **E6:** create `frontend/src/features/editing/` for code shared by C, B and the advanced editor:
  - `ComposeStage`;
  - `LineFixStage`, `LineFixCanvas` and zoom;
  - `editsQueue`, `pendingWrites` and `placementSaver`;
  - `usePageText`;
  - `ExclusiveChips`;
  - `ExportOptions`;
  - `errMsg`;
  - the chip styles, extracted into `chips.module.css`.

  After this, `features/fix` never imports from `features/editor`, and `features/editor` may import from `features/editing`. Pin it with an ESLint `no-restricted-imports` rule.
- **D5:** split FixPage into:
  - `FixPage.tsx` (load + FixBody layout, ≤ 250 lines);
  - `useFixActions.ts`;
  - `usePlacementSaves.ts`;
  - `FixPreview.tsx`;
  - `rows/{Row,PoseRow,BackgroundRow,LinesRow,CaptionRow}.tsx`.
- **E16:** extract `useEditorNav` and `useEditorAutoCompose`, the latter including the loss-ask dialog, plus `<ComposeStageArea>` from EditorPage, leaving EditorPage ≤ 300 lines.
- **D11, D12, D13, E14, E15:** as the findings say:
  - `isActive` is exported once from `hooks.ts`;
  - `NO_QUOTA` moves to `lib/limits.ts`;
  - BookOverview uses `fixHref`;
  - `quiet` and `useSingleFlight` move to `lib/`;
  - `features/story/*` moves to `features/setup/`;
  - `CreateBookDialog.module.css` is merged into `BookSettingsDialog.module.css`.

- [ ] Move and split. The only test edits are import paths. Run the frontend checks and e2e after each sub-step. Commit per sub-step: `refactor(frontend): …`.


### Task 7: Acceptance — Claude walks the real flow in the browser (controller, after the final review)

Same as the previous plan's Task 18:
- run the acceptance start script (separate DB, ports 8001/3001, `DEV_ALLOWED_ORIGINS` set), with the real ComfyUI when the GPU is idle;
- tell the user before using the GPU;
- walk: new book → character → paste a 3-page story using the one-line 「中文：…　英文：…」 format → 開始做這本書 → B → C (arrow keys, 英文 off, 復原) → 進階編輯 換一張 → C 復原 → print the PDF;
- assert 3 pages with split captions, then run `/free`, write NOTES.md, and send the screenshots.
