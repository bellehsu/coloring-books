# Code Review Fixes (2026-11-12) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the review findings the user selected (packages 1, 2, 3, 4 and 6), then verify the product by walking the real UI flow in a browser.

**Architecture:** Bug and security fixes come first (Tasks 1–10), each with a regression test. Deletions and small leftovers follow (11–13), then structural moves (14–17) that must not change behaviour. Last is the acceptance walkthrough (18). Moves come last so they don't conflict with the fixes.

**Tech Stack:** FastAPI, SQLModel/SQLite, Alembic, pytest; Next.js 16, React 19, TanStack Query, Vitest, Playwright.

**Spec:** the review reports `docs/reviews/2026-11-12/{A-backend-core,B-backend-api-security,C-image-pipeline-scripts,D-frontend-main,E-frontend-editor}.md`. Each finding there has a location, a failure scenario and a suggested fix. **Read the finding text for every ID your task lists before you start.** Where this plan picks one of several suggested fixes, this plan wins.

## Global Constraints

- Project rules are in `CLAUDE.md` (zh-Hant). All UI copy is in Traditional Chinese.
- Branch `review-fixes` in the main checkout `/ai-data/repos/coloring-books`. Local commits only, never push. Do not create git worktrees.
- Every commit ends with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`
- Never read or modify `storage/`, `storage-r2/`, `storage-backup-*` or `scripts/dev.env` (it holds secrets). Do not start `scripts/dev.sh`. Do not contact ComfyUI (8188) in Tasks 1–17.
- Backend tests: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`. The ROS PYTHONPATH breaks collection.
- Frontend checks: `cd frontend && npm test && npm run lint && npx tsc --noEmit`.
- e2e: `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e` (ports 8010/3010).
- After any API shape change, regenerate the frontend types with `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run gen:api` and commit `openapi.json` + `schema.d.ts`.
- Design tokens only in CSS modules: no hex or rgba, and `--radius-*` tokens for radii from 4 to 14 px (`designTokens.test.ts`).
- Decisions made by the user, which must not be reverted:
  - **A3** stays as it is (any failure of a generation bound to a page marks `attention="failed"`).
  - **E2:** the advanced editor's 「換一張」 keeps auto-swapping, but must be undoable.
  - **C1, C3, C4** are out of scope.
- Structural tasks (14–17) must not change behaviour. Their only test changes are import paths and renames.

## Review Focus

1. **Same page open in C and in 進階編輯 in two tabs.** Edits from either tab must not lose strokes or placement. Task 7 adds an e2e that does this.
2. **Requests proxied through Next.js rewrites** (`/api/*` from port 3000 to 8000). The body-size and cross-site guards must not block normal same-origin use through the proxy, including chunked uploads with no Content-Length. Task 3 tests both.
3. **Legacy pages** (pre-Phase-13 pages with `auto_*` null; old whole-page `page` generations) going through the new E2 snapshot path must not crash or snapshot nonsense. Task 6 tests a legacy page.
4. **Revoking an owner while one of their jobs is RUNNING, not QUEUED.** The running job finishes, and nothing new starts for that owner. Task 4 tests it.
5. **Holding an arrow key down on C** (key repeat about 30/s). This must give exactly one undo step per pause, not one per keypress. Task 9 tests it.

---

### Task 1: Generation queue: classify ComfyUI errors and cap requeues (A1, A2)

**Files:** Modify `backend/app/services/comfy_client.py`, `backend/app/services/comfy_engine.py`, `backend/app/services/worker.py`. Test `tests/test_comfy_client.py`, `tests/test_worker.py`.

**Decisions:**
- Only transport-level failures are "unavailable". That means `httpx.ConnectError`, `httpx.ConnectTimeout`, `httpx.PoolTimeout` and `httpx.RemoteProtocolError`, plus "WS connect refused/timeout" when polling also cannot connect. These raise `ComfyConnectionError`.
- A read timeout stays `ComfyTimeoutError`.
- Any HTTP non-200 response (`_get_json`, `list_models`, `upload_image`, `download`, `/prompt`) raises plain `ComfyError` with status code and path. The job FAILS and is refunded through the existing failure path.
- Requeue cap: the worker counts engine-unavailable requeues in `request_json["engine_requeues"]` (int, default 0).
  - On the **4th** unavailable result for the same generation, mark it FAILED.
  - The message is 「繪圖服務一直連不上，已停止重試」.
  - Refund it like any failure. Add `engine_requeues` to the protected control keys next to `take` and `usage_*`.

- [ ] Write failing tests:
  - `test_http_500_on_download_is_error_not_connection`: a mocked transport returns 500 for `/view` → `ComfyError`, and `not isinstance(e, ComfyConnectionError)`.
  - `test_connect_timeout_is_connection_error`: `httpx.ConnectTimeout` → `ComfyConnectionError`.
  - `test_read_timeout_stays_timeout`.
  - `test_unavailable_requeue_cap`: a fake engine always raises `EngineUnavailable`; run the worker 4 times. The generation is FAILED, `error == "繪圖服務一直連不上，已停止重試"`, and usage is refunded.
  - `test_http_error_fails_job_once`: an engine HTTP 404 → FAILED after one attempt, no requeue.
- [ ] Run them and see them fail. Implement. Run `tests/test_comfy_client.py tests/test_worker.py tests/test_comfy_engine.py`, then the full backend suite.
- [ ] Commit `fix(worker): HTTP errors fail the job; only transport errors requeue, capped at 3 (A1, A2)`.

### Task 2: Worker robustness and error privacy (A4, A5, A6, A7)

**Files:** `backend/app/services/worker.py`, `backend/app/services/comfy_engine.py`, `backend/app/api/generations.py` (and any other place that returns a generation `error` to non-admins). Tests `tests/test_worker.py`, `tests/test_comfy_engine.py`, `tests/test_api_generations.py`.

**Decisions:**
- **A4:**
  - `_finish` and `_fail` retry their DB write up to 3 times, with back-off of 0.2, 0.5 and 1.0 s.
  - If all three fail, the gen id goes into `self._unfinished: dict[int, Callable]`, and those are retried at the top of each loop iteration before `_claim`.
- **A5:** `stop()` calls `engine.interrupt()` if a job is running.
  - Add `async def interrupt(self) -> None` to the `GenerationEngine` protocol; it is a no-op in the fakes.
  - `ComfyEngine` calls `ComfyClient.interrupt()` with a 3 s timeout and ignores errors.
- **A6:** always upload the character image for every POSE job (`overwrite=True`). Delete the upload cache.
- **A7:** non-admin API responses replace any engine-unavailable or transport error text with 「繪圖服務暫時無法連線，稍後會自動重試」. Admins (`owner.is_admin`) still see the raw text.

- [ ] Write failing tests:
  - `test_finish_write_retried_then_succeeds`: monkeypatch the session commit to fail twice, then succeed. The row ends SUCCEEDED, not RUNNING.
  - `test_stop_interrupts_running_engine`: the fake engine records `interrupt` calls → 1.
  - `test_pose_uploads_character_every_time`: two POSE jobs → 2 uploads.
  - `test_engine_error_text_hidden_from_non_admin`: queue status / generation out for a normal owner shows the fixed sentence; admin sees the raw `127.0.0.1:8188` text.
- [ ] Implement. Run the targeted tests, then the full suite. Commit `fix(worker): retry finish writes, interrupt on stop, re-upload character, hide engine errors from testers (A4–A7)`.

### Task 3: Request guards: body size, cross-site writes, signed-URL TTL (B1, B2, B11)

**Files:** Create `backend/app/api/guards.py` (two ASGI middlewares). Register them in `backend/app/main.py`. Modify `backend/app/config.py` (B11). Test `tests/test_guards.py`.

**Decisions:**
- **`BodyLimitMiddleware`:**
  - Limits: paths ending in `/upload` get `MAX_BG_UPLOAD_BYTES + 1_000_000`, importing the existing 10 MB constant from `api/compose.py`; Task 14 renames it to `MAX_UPLOAD_BYTES` later. Every other `/api/` path gets 2_000_000. Other paths are not limited.
  - It rejects with 413 `{"detail": "檔案太大"}` when `Content-Length` exceeds the limit.
  - Otherwise it counts streamed bytes and aborts with 413 once they exceed the limit. This covers chunked uploads.
- **`CrossSiteWriteMiddleware`:**
  - For POST, PUT, PATCH and DELETE, if the `Sec-Fetch-Site` header is `cross-site`, return 403 `{"detail": "不接受從其他網站送來的修改"}`.
  - Missing header (scripts, tests, curl) and `same-origin`/`same-site`/`none` are allowed.
  - Do **not** compare Origin and Host: the Next.js rewrite proxy changes Host.
- **B11:** `storage.signed_url_ttl_s` becomes `Field(120, ge=90)`. This is 60 s redirect cache plus a 30 s margin; reference the constant from `api/files.py` in the comment.

- [ ] Write failing tests:
  - `test_upload_over_limit_413_before_auth`: no cookie, a 12 MB body to `/api/pages/1/backgrounds/upload` → 413, not 401. The handler is never reached (monkeypatch it).
  - `test_chunked_upload_over_limit_413`.
  - `test_json_over_2mb_413`.
  - `test_normal_upload_passes`: a 1 MB upload behaves as before.
  - `test_cross_site_post_403`.
  - `test_same_origin_and_missing_header_allowed`.
  - `test_get_cross_site_allowed`.
  - `test_ttl_below_90_rejected`.
- [ ] Implement. Run the targeted tests and the full suite. Then run e2e to prove that the proxied browser flows still work. Commit `fix(security): body size limit, cross-site write guard, TTL floor (B1, B2, B11)`.

### Task 4: Invites: revoke cancels queued work; account-switch confirmation (B9, B10)

**Files:**
- `scripts/invites.py` and `backend/app/services/auth.py` (move the cancel logic into a shared function `cancel_owner_queue(session, owner_id) -> int` and reuse it from delete-data).
- `backend/app/api/invites.py`.
- Create `frontend/src/app/switch-account/page.tsx` and its test.
- Tests in `tests/test_invites_script.py` and `tests/test_auth.py`.

**Decisions:**
- **B9:** revoke runs, in the same transaction:
  - QUEUED generations of the owner's books become CANCELLED, with a refund through `usage.refund_generations`;
  - queued PageTasks become cancelled;
  - RUNNING jobs are left to finish.
- **B10:**
  - `GET /i/{token}` keeps its current behaviour when the browser has no valid session, or a session for the **same** owner.
  - If there is a valid session for a **different** owner, it redirects 303 to `/switch-account#<token>` instead of replacing the cookie. The fragment never reaches the server or logs.
  - That page shows 「這個連結是「{name}」的。要切換成「{name}」嗎？目前是「{current}」。」 with two buttons: 「切換」 and 「留在目前的身分」.
    - 「切換」 POSTs `/api/invites/accept {token}`. The new endpoint sets the cookie exactly as the GET does, returns `{name}`, and returns 404 for a bad or revoked token.
    - 「留在目前的身分」 goes to `/`.
  - The page needs the invite's name before accepting: add `GET /api/invites/peek` with header `X-Invite-Token`, returning `{name}`. It also returns 404 for a bad token.

- [ ] Write failing tests:
  - `test_revoke_cancels_queued_and_refunds`.
  - `test_revoke_leaves_running_job` (Review Focus 4).
  - `test_invite_with_other_session_redirects_to_switch`.
  - `test_invite_same_owner_or_no_session_sets_cookie`.
  - `test_accept_post_sets_cookie`.
  - `test_peek_bad_token_404`.
  - Vitest for the switch page: it shows both names, 切換 calls accept and then navigates to `/`, and 留在 goes to `/` without calling accept.
- [ ] Implement, regenerate the types, run backend and frontend checks. Commit `fix(auth): revoke cancels queued jobs; confirm before switching identity (B9, B10)`.

### Task 5: Image robustness (C5, C14, C6)

**Files:** `backend/app/services/compose.py`, `backend/app/services/lineart_render.py`, `backend/app/services/autofix.py`, `scripts/dev_fake_backend.py`. Tests in `tests/test_compose.py`, `tests/test_lineart_render.py`, `tests/test_autofix.py`, `tests/test_scripts.py`.

**Decisions:**
- **C5:** `compose()` and `largest_component` raise `ValueError` for a mask bbox whose short side is under 16 px or whose aspect ratio is over 8:1. The existing handler turns that into 「請重新產生角色動作」. `target_w` is additionally clamped to `3 × background width`.
- **C14:**
  - `apply_strokes` rasterizes each stroke only inside its bounding box, padded by the stroke width, and output stays identical.
  - `survives()` compares only the stroke-bbox sub-array.
  - A test asserts pixel-identical output against the old implementation, copied into the test as `legacy_apply_strokes`, for 200 random strokes on an A4 canvas.
- **C6:** `dev_fake_backend.py` refuses to start (exit 2, message in zh) when `--storage` resolves to:
  - the configured `paths.storage_dir`;
  - the parent of `paths.database`;
  - any folder containing `app.db` but no `.fake-backend` marker.

  It creates the marker in folders it initializes.

- [ ] Write failing tests:
  - `test_extreme_mask_rejected`;
  - `test_target_width_clamped`;
  - `test_apply_strokes_matches_legacy`;
  - `test_survives_matches_legacy`;
  - `test_fake_backend_refuses_real_storage`, using a tmp settings file whose storage_dir is a tmp dir with an `app.db`.
- [ ] Implement. Run the targeted tests and the full suite. Commit `fix(images): reject degenerate masks, bbox-limited stroke raster, fake backend storage guard (C5, C14, C6)`.

### Task 6: Advanced editor 「換一張」 is undoable (E2, backend)

**Files:** `backend/app/services/pipeline.py` (`auto_select` / `on_generation_succeeded`), `backend/app/services/history.py`. Test `tests/test_api_history.py`.

**Decision:**
- When `on_generation_succeeded` → `auto_select` **replaces** a slot that already held a different generation on a page whose `selected_generation_id` is set, call `history.push_snapshot(session, page)` before the swap, in the same transaction.
  - Fill-only runs, where the slot was empty, push nothing.
  - So does the C take path, which already snapshots in `take_generation`.
- The user strokes on the old composite stay in the snapshot, so 「↶ 復原」 in C restores the slot, the composite and the strokes.
- Legacy pages (`auto_*` null) keep today's rule: a user pick is never replaced.

- [ ] Write failing tests:
  - `test_old_editor_regenerate_swap_is_undoable`: a ready page, user strokes drawn through the non-oneclick PUT, then `gen_pose` (non-take) succeeds → the slot is swapped, `history_rows` +1. After `drain`, `undo` brings the old pose id back, and `user_layer` of the restored composite equals the strokes.
  - `test_fill_only_pushes_no_snapshot`.
  - `test_legacy_page_without_auto_not_swapped_no_snapshot` (Review Focus 3).
- [ ] Implement. Run `tests/test_api_history.py tests/test_pipeline.py`, then the full suite. Commit `fix(history): auto-swap from a new generation pushes an undo snapshot first (E2)`.

### Task 7: Advanced editor agrees with C (E1, E3, E5, E7, E8)

**Files:** `frontend/src/features/editor/{EditorPage,Filmstrip,FinishPanel,placementSaver,editsQueue,ComposeWorkspace,CaptionPreview}.tsx/ts`, `frontend/src/lib/checklist.ts`. Tests in the matching `*.test.ts(x)` files, plus a new `frontend/e2e/editor-c-consistency.spec.ts`.

**Decisions:**
- **E1:** while `page.recompose_pending`, the advanced editor:
  - shows 「這一頁正在重新合成…好了會自動更新」 in the stage header;
  - disables the ② 補線 tab and the auto-compose;
  - polls pages every 2 s, through the `usePagesFollowJobs` refetch interval, until the flag clears.
- **E3:** `placementSaver` returns its cached `latest` placement only while that page has a write in flight or failed (`hasPending(pageId)`). Otherwise the editor uses the server's `page.placement`.
- **E5:**
  - Filmstrip and checklist show `attention` as 「請看一下」 and `recompose_pending` as 「製作中」, using the same labels as B.
  - FinishPanel's 「完成」 calls `POST /api/pages/{id}/accept`, the same as C's 「這頁可以印了」, instead of `/pass`.
  - The editor's export drawer uses `variant="book"`, so it prints ready pages only.
- **E7:** before writing, `flushEdits` in both the editor and C awaits **both** queues for the same page and generation: `peekEditsQueue(qc, pageId, gid)` and `peekEditsQueue(qc, pageId, gid, { oneclick: true })` (keys `${pageId}:${genId}` and `${pageId}:${genId}:c` in `editsQueue.ts`). Add a helper `flushAllEditsFor(qc, pageId, gid): Promise<void>` in `editsQueue.ts`, and have both sides call it.
- **E8:** `CaptionCard` shows 「這行目前不印（在處理頁面可打開）」 under a line whose `caption_show_*` is false, and the 「會印」 hint is hidden for that line.

- [ ] Write failing vitest tests, one per decision:
  - `EditorPage` with `recompose_pending: true` → the 補線 tab has `aria-disabled` and the notice is shown;
  - placementSaver idle → returns the server placement;
  - Filmstrip renders 請看一下;
  - FinishPanel posts `/accept`;
  - CaptionCard with `caption_show_en: false` shows the notice.
- [ ] Write an e2e test: open C and 進階編輯 for the same page in two tabs. In C, drag the character. In the editor, the 補線 tab is disabled until the recompose finishes, then the new placement shows (Review Focus 1).
- [ ] Implement. Run the frontend checks and e2e. Commit `fix(editor): respect C's recompose, server placement, attention, accept and caption toggles (E1, E3, E5, E7, E8)`.

### Task 8: Export and navigation (E4, E9, E10, D8)

**Files:** `frontend/src/features/editor/{pendingWrites.ts,ExportDrawer.tsx,EditorPage.tsx}`, `frontend/src/components/shell/BookBar.tsx`. Tests in the matching test files.

**Decisions:**
- **E4:** when a lines write fails, the export block message names the page and links to it: 「第 {n} 頁的修線還沒存好」 plus a link 「去處理」 to `/books/{id}/page/{pageId}`. ExportOptions also offers 「重送」, which re-pushes the latest edits of the failed queues.
- **E9:** export awaits `placementSaver.whenIdle()` for all pages and any pending auto-compose before building.
- **E10:**
  - The book-title crumb links to `/books/{id}`.
  - The editor stage header gets a link 「回到處理這一頁」 to the current page's C.
- **D8:** after the summary refresh and after a PDF download, ExportOptions also invalidates `qk.overview(bookId)`.

- [ ] Write failing tests:
  - the export block shows the page number and link;
  - 重送 re-pushes;
  - export waits for the placement saver (a fake saver resolves later; the request goes out after it);
  - the crumb href;
  - overview invalidated.
- [ ] Implement, run checks, commit `fix(export): actionable write errors, wait for placement, links back, overview refresh (E4, E9, E10, D8)`.

### Task 9: C page reliability (D2, D3, D4, D6, D7)

**Files:** `frontend/src/features/fix/FixPage.tsx`, `frontend/src/api/hooks.ts`. Tests `FixPage.test.tsx`, `hooks.test.ts`.

**Decisions:**
- **D2:** `usePlacementSaves` coalesces with latest-wins.
  - At most one PUT is in flight. Changes made meanwhile replace a single pending value, which is sent when the in-flight PUT settles.
  - Keyboard moves are also debounced at 400 ms, so a held arrow key gives one PUT after release.
- **D3:**
  - Text and toggle saves register their promise in a `pendingSaves` ref on FixBody.
  - `runHistory`, `accept` and `retry` first `await Promise.allSettled([...pendingSaves])`.
  - `busy` is true while any are pending.
- **D4:** FixBody registers `beforeunload` when edits queues have pending or failed writes, placement saves are in flight or pending, or text saves are pending. Reuse `onBeforeUnload` from `pendingWrites.ts`.
- **D6:** when `useQA` errors, DrawStage and LinesRow show `errMsg(error, "線稿載入失敗")` and a 「再試一次」 button that calls `refetch`.
- **D7:** `useBookOverview(bookId, { background?: boolean })`. Only B passes `background: true`; C does not poll in hidden tabs.

- [ ] Write failing tests:
  - `holding arrow key yields one placement PUT after release` (fake timers, 30 keydown events → 1 PUT; Review Focus 5);
  - `rapid drags send at most one in-flight PUT and the last value wins`;
  - `undo waits for a pending caption save` (ordering asserted from the fetch call log);
  - `beforeunload set while placement in flight`;
  - `QA error shows message and retry`;
  - `C overview does not poll in background`.
- [ ] Implement, run checks and e2e, commit `fix(c-page): coalesce placement saves, wait for text saves, unload guard, QA error, no background poll (D2–D4, D6, D7)`.

### Task 10: New-book back navigation (D1)

**Files:** `frontend/src/features/setup/SetupPage.tsx`, `frontend/src/app/books/new/page.tsx`. Tests `SetupPage.test.tsx` and a new e2e `frontend/e2e/setup-back.spec.ts`.

**Decision:**
- On mount, `/books/new` reads `window.location.pathname`. If it matches `^/books/(\d+)/setup$`, use that id as the initial `bookId` and load that book. This is the history entry rewritten by `replaceState`.
- Keep the `replaceState` approach so in-progress drafts survive.

- [ ] Write failing tests:
  - Vitest: `/books/new` rendered with `location.pathname = "/books/5/setup"` loads book 5 and does not show 「做一本新的著色書」.
  - e2e: type a title on `/books/new` → press 開始做這本書 → `page.goBack()` → the heading shows the book title, and the books list count is unchanged.
- [ ] Implement, run checks and e2e, commit `fix(setup): back navigation after creating a book shows that book (D1)`.

### Task 11: Backend dead code (A9, A10, A11, B3, B4, B5, B6, B12, C9)

**Files:** as listed in each finding.

**Decisions:** delete exactly what each finding names.
- **B3:**
  - Remove the lineart-preview endpoint, `ensure_preview`/`_preview` and `Storage.preview`.
  - Keep the storage_gc one-off rule that deletes everything under `cache/previews/`, commented as a one-off clean-up of old objects.
  - Note that C9's suggestion to "test `ensure_preview`" no longer applies once preview is gone, so tests for `render_preview` are simply deleted.
- **B4:** request models keep Pydantic's default ignore-extra behaviour, so old clients sending removed fields don't break.
- **B6:** remove `BookStats.needs_fix`, `BookStats.no_selection`, `QueueStatus.running_id` and the other fields B6 lists. Before removing each one, grep `frontend/src` and `frontend/e2e` once more.
- **A11:** remove `GenerationRequest.negative_prompt`, and keep the DB column.

- [ ] Delete. Update the tests that only covered deleted code (delete them) and fixtures that mention removed fields. Regenerate the API types and fix the TS fallout (fields removed from fixtures).
- [ ] Run the full backend suite and the frontend checks. Commit `chore: remove unused endpoints, fields and helpers (A9–A11, B3–B6, B12, C9)`.

### Task 12: Frontend dead code (D9, D10/E11, E12, E13)

**Files:** `frontend/src/api/hooks.ts`, `frontend/src/api/client.ts`, `frontend/src/components/shell/BookBar.tsx`, `frontend/src/features/editor/{placementSaver.ts,LineFixCanvas.tsx,StageHeader.tsx}`.

**Decisions:**
- Delete the 4 hooks and `BatchMaterials`.
- BookBar is now used only by the advanced editor. Shrink it to:
  - the title crumb (linked in Task 8);
  - one link 「主角與故事」 to `/books/{id}/setup`;
  - 匯出;
  - 設定.

  Remove the `current` prop values that can't occur.
- Delete `busy()`/`busyPage()`, the ZOOM re-export and the legacy mode map. Unknown modes fall back to 構圖.

- [ ] Delete, update the tests, run the frontend checks and e2e. Commit `chore(frontend): remove unused hooks, BookBar branches, legacy mode map (D9, D10, E11–E13)`.

### Task 13: Small leftovers (A8, A12, B7, B8, C12, C13)

**Decisions:**
- **A8:** wrap each workflow's check in `except Exception` → `load_errors`. A JSON error in `list_models` becomes `ComfyConnectionError`. If a registry reload fails, report it in the report and keep the old registry.
- **A12:** `comfy.models_root` becomes optional (`Path | None = None`). `validate_paths` stops checking `models_root`/`install_dir`. `scripts/sync_comfy_model_paths.py` errors clearly when it is unset.
- **B7:** `storage_gc` clears `image_path` (and deletes the object) for COMPOSITE generations that:
  - are not any page's `selected_generation_id`;
  - are older than 7 days;
  - are not the newest 3 composites of their page.
- **B8:** shared PDF skips pages that fail with `LineartUnavailable` or the caption font, and returns 404 only if no page renders.
- **C12:** back up to `.bak` only if `.bak` doesn't exist yet. The docstring says comments and formatting get rewritten.
- **C13:** unknown `--workflow` names print the available names and exit 2.

- [ ] For each item, write a failing test, then implement:
  - `test_preflight_workflow_exception_reported`;
  - `test_settings_without_models_root_load`;
  - `test_gc_clears_old_unselected_composites_keeps_newest_three`;
  - `test_shared_pdf_skips_unrenderable_page`;
  - `test_sync_keeps_first_backup`;
  - `test_preflight_unknown_workflow_exit_2`.
- [ ] Run the full suite. Commit `fix: preflight robustness, optional models_root, GC old composites, partial shared PDF, script fixes (A8, A12, B7, B8, C12, C13)`.

### Task 14: Backend services split (B13, B15, B16, A13, A14)

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

### Task 15: Backend schemas, names, helpers and pipeline internals (B14, C7, C8, C10, C11, C2)

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

### Task 16: Backend performance internals (A15, A16, A17)

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

### Task 17: Frontend structure (E6, D5, E16, D11, D12, D13, E14, E15)

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

### Task 18: Acceptance: Claude walks the real flow in the browser (user requirement)

**Not a subagent task.** The controller does it after the final whole-branch review.

- [ ] Start acceptance servers from the branch:
  - backend on 8001 with a fresh settings copy whose `paths.database`/`storage_dir` point to a new folder `experiments/results/review_fixes_acceptance_<ts>/storage`, `storage.backend: local`, and `comfy.url` = 8188;
  - frontend `next dev -p 3001` with `BACKEND_URL` = 8001.
- [ ] Check `nvidia-smi` and tell the user the GPU is about to be used.
- [ ] Drive Playwright through these steps, taking screenshots at each:
  1. new book → title → character (text) → paste a 3-page story → 開始做這本書;
  2. B overview until all pages are done;
  3. C on page 1: move the character with the arrow keys (one undo step), toggle 英文 off, 復原;
  4. 進階編輯 on the same page: 換一張 for the pose → wait → C 復原 brings strokes back;
  5. 印出這本書 → PDF downloaded.
- [ ] Afterwards: `POST /free` on ComfyUI.
- [ ] Write `experiments/results/review_fixes_acceptance_<ts>/NOTES.md` with what passed, what failed, timings and screenshots.
- [ ] Send the screenshots to the user.
