# Fix Round 2026-11-13 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the eight user decisions of the 2026-11-13 fix round: delete a book, delete character candidates, progress counted in pages, pose redraw UX (batch and single page), single-outline line art, a copy-prompt fallback, editing existing pages in setup, and thinner outlines in the generation prompts.

**Architecture:** One Alembic migration (0015) carries every schema change. Backend tasks come first, each adding its endpoint or service with pytest coverage and regenerating the frontend API types. Frontend tasks follow, with Vitest coverage and the four e2e flows the spec names. The last task is GPU-gated: it calibrates the #8 prompt wording and walks the real UI, and it runs only after the user frees the GPU.

**Tech Stack:** FastAPI, SQLModel/SQLite, Alembic, OpenCV/NumPy, pytest; Next.js 16, React 19, TanStack Query, Vitest, Playwright (fake backend `scripts/dev_fake_backend.py`).

**Spec:** `docs/superpowers/specs/2026-11-13-fixes-round-design.md`, with its mockups in `docs/superpowers/specs/mockups/2026-11-13/` (`A_overview_select.png`, `B_redraw_panel.png`, `C_delete.png`). The spec is the binding authority. Where this plan rules on something the spec leaves open, the ruling is marked **Ruling**.

## Global Constraints

- Project rules are in `CLAUDE.md` (zh-Hant). Work on branch `fixes-2026-11-13` in the main checkout `/ai-data/repos/coloring-books`. Never create git worktrees or sibling folders.
- **No GPU and no ComfyUI in any test or in Tasks 1–15.** Every test mocks the engine (`tests/fake_engine.py`, the e2e fake backend). Only Task 16 touches ComfyUI (8188), and only after the user explicitly says the GPU is free.
- Local commits only. Never push.
- Every commit message ends with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`
- Never read, write or delete anything under `storage/`, `storage-r2/` or `storage-backup-*`. Never read `scripts/dev.env` (it holds secrets). Do not start `scripts/dev.sh`.
- UI copy is zh-Hant, exactly as the spec gives it. Every string this plan quotes in 「」 is the literal UI text, without the brackets unless the brackets are part of the text.
- Every module gets tests. Backend: pytest. Frontend: Vitest. A flow the spec lists gets an e2e test.
- Backend tests: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q` (the ROS PYTHONPATH breaks collection).
- Frontend checks: `cd frontend && npm test && npm run lint && npx tsc --noEmit`.
- e2e: `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e` (ports 8010/3010, fake backend, no GPU).
- After any API shape change, regenerate types with `cd frontend && PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run gen:api`, and commit `src/api/openapi.json` and `src/api/schema.d.ts` in the same task. If `npx tsc --noEmit` then fails only because a test fixture lacks a new required field, add the field to that fixture in the same task (for example `src/features/fix/testData.ts` or the `pg()`/`ov()` helpers in `src/features/book/BookOverview.test.tsx`).
- CSS modules use design tokens only: no hex or rgba, and `--radius-*` tokens for radii (`src/app/designTokens.test.ts`).
- `RENDER_VERSION` and `QA_VERSION` stay at their current values. `tests/test_pipeline_byte_identity.py` must stay unchanged and green.
- A new route that carries `book_id`, `page_id` or `gen_id` is added to an explicit set in `tests/test_ownership.py`, next to `PHASE13_ROUTES` and asserted the same way.

## Review Focus

1. **An admin deletes another owner's book.** `owned_book` lets admins through, but the spec says admins may delete only their own books. Expected: 404 「找不到這本繪本」, and nothing is deleted. The test is in Task 4.
2. **A batch redraw that exceeds today's remaining cap** (for example 5 left, 2 pages × 3). Expected: 429 「今天只剩 5 次，這次需要 6 次」 (the existing `usage._over_cap_detail` message). Nothing is queued, usage is unchanged and no page is tagged. The test is in Task 7.
3. **An old page (`outline_fill=False`) whose pipeline reruns** after a paper-size change or 繼續做. The rerun reuses the existing composite. Expected: `outline_fill` stays False, `lineart_key` is unchanged and nothing re-renders. The test is in Task 2.
4. **Redraw pressed twice on the same page** (two batch calls, or batch then inline) before the first finishes. Expected: both takes land in order, the page ends on the later one, every pose stays in the history row, and ↶ 復原 goes back one pose at a time. The test is in Task 7.
5. **`localStorage` throws** (private window, blocked site data) while C renders the pose history row. Expected: the row renders without 「新」 badges and nothing crashes. The test is in Task 14.

---

### Task 1: Migration 0015 and model fields

**Files:**
- Create: `backend/migrations/versions/0015_fix_round.py`
- Modify: `backend/app/models.py` (`Book`, `Page`)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `Book.__table_args__ = {"sqlite_autoincrement": True}`, so a deleted book id is never reused.
  - `Page.outline_fill: bool = Field(default=True, sa_column_kwargs={"server_default": false()})`. New ORM rows are True; rows that already exist when the migration runs are False.
  - `Page.redraw_background: bool = Field(default=False, sa_column_kwargs={"server_default": false()})`
  - `Page.redraw_pose: bool = Field(default=False, sa_column_kwargs={"server_default": false()})`
  - Also update the `Page.attention` comment: its values are now `"failed"` and `"review_pose"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_0015_fix_round_roundtrip(tmp_path):
    # upgrade to "0014"; insert owner, book id 1, one page (same INSERTs as test_0014_page_caption_show_roundtrip)
    # upgrade to "0015"
    #   cols = _cols(c, "page"): outline_fill / redraw_background / redraw_pose all nullable False
    #   SELECT outline_fill, redraw_background, redraw_pose FROM page == (0, 0, 0)
    #   "AUTOINCREMENT" in table_sql(c, "book") and in table_sql(c, "page")
    #   the book row still exists with title 'x'
    #   DELETE FROM book WHERE id=1 (after deleting its page); INSERT another book -> its id == 2
    # downgrade to "0014"
    #   the three columns are gone; page keeps AUTOINCREMENT; book has no AUTOINCREMENT
    #   PRAGMA integrity_check == "ok"
    # upgrade to "head"

def test_fresh_db_book_uses_autoincrement(tmp_path):
    # init_db; "AUTOINCREMENT" in table_sql(c, "book")

def test_orm_new_page_defaults(tmp_path):
    # Page(book_id=..., position=0) committed -> outline_fill is True, redraw_background is False, redraw_pose is False
```

Leave the existing `AUTOINCREMENT_TABLES` tuple alone. Older revision tests iterate over it at revisions before 0015.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q tests/test_migrations.py`
Expected: FAIL, because revision 0015 does not exist.

- [ ] **Step 3: Write the migration and the model fields**

- `upgrade()`: `batch_alter_table('page', table_kwargs=_AUTOINC)` adds the three NOT NULL Boolean columns with `server_default=sa.false()`. Then `batch_alter_table('book', recreate="always", table_kwargs={"sqlite_autoincrement": True})` with an empty body, the same way 0003 rebuilt page and generation.
- `downgrade()`: reverse the two steps (page `recreate="always", table_kwargs=_AUTOINC`; book rebuilt with `sqlite_autoincrement: False`).
- The docstring follows 0014's style.

- [ ] **Step 4: Run the whole migration file and `test_schema_matches_models`. Expected: PASS.**

- [ ] **Step 5: Commit** with `feat(db): migration 0015 — page outline_fill/redraw flags, book AUTOINCREMENT`.

---

### Task 2: Single-outline line art (#5)

**Files:**
- Modify: `backend/app/services/lineart_extract.py`, `backend/app/services/lineart_render.py`, `backend/app/services/page_lineart.py` (`lineart_key`, `ensure_page_lineart`, `ink_before_user_layer`), `backend/app/services/page_compose.py` (`compose_page`)
- Test: `tests/test_lineart_extract.py`, `tests/test_lineart_render.py`, `tests/test_page_lineart.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Page.outline_fill` (Task 1).
- Produces, all in `app/services/lineart_extract.py`:
  - `STROKE_FILL_LUMA = 70`, `STROKE_FILL_RADIUS = 10`, `STROKE_FILL_KEEP = 13.0` and `STROKE_FILL_REF_WIDTH = 832`.
  - `OUTLINE_FILL_VERSION = 1`. Bump it whenever the four constants or the algorithm change.
  - `stroke_fill(rgb: np.ndarray, gray: np.ndarray, t: int, r: int, keep: float) -> np.ndarray`. The body is copied verbatim from `experiments/single_outline.py::stroke_fill`:
    - `dark = luma < t`;
    - `thick = open(dark, ellipse 2r+1)`;
    - `dt = distanceTransform(dark, L2, 5)`;
    - per 8-connected component of `thick`, take the max `dt`; the component is `solid` when that max is ≥ `keep`;
    - the output is `gray` with `out[dark & ~solid] = 0`.
  - `stroke_fill_params(width: int) -> tuple[int, float]` returns `(max(1, round(10 * width / 832)), 13 * width / 832)`.
  - `fill_outlines(img: np.ndarray, gray: np.ndarray) -> np.ndarray` converts `img` (RGB, RGBA or gray) with `_rgb`, then applies `stroke_fill` with `t=STROKE_FILL_LUMA` and `stroke_fill_params(img.shape[1])`.
- Produces in `lineart_render.py`: `render_lineart(color, content_px, detail, line_width_px, *, outline_fill: bool = False) -> np.ndarray`. When True it runs `fill_outlines(color, xdog_gray)` before `lineart_post.render_ink`. `render_base` and `render_params()` do not change.
- Produces in `page_lineart.py`:
  - `lineart_key(gen, page, content_px, strokes_json)` appends `"outline_fill", OUTLINE_FILL_VERSION` to the digest parts **only when** `getattr(page, "outline_fill", False)` is true, so every existing key stays byte-identical.
  - Both `render_lineart` calls pass `outline_fill=page.outline_fill`.
- Produces in `page_compose.compose_page`: when it writes a **new** composite (`written is not None`), the final `UPDATE page` also sets `outline_fill=True`. Reusing an existing composite does not change the flag.

**Ruling:** "the composite changes" means "a new composite generation is written". That covers a new or redrawn pose or background, 換一張, an upload, a placement drag, and a 繼續做 recompose with changed inputs. An undo that reselects an older composite leaves the flag as it is.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lineart_extract.py
def test_stroke_fill_params_scale_with_width():
    assert stroke_fill_params(832) == (10, 13.0)
    assert stroke_fill_params(1664) == (20, 26.0)
    assert stroke_fill_params(416) == (5, 6.5)

def test_stroke_fill_thick_band_gives_one_line():
    # 832x600 white RGB with a horizontal black band 16 px thick across the middle
    # gray = extract_xdog(rgb, 1.2); content_px (832, 600), width 11
    # count ink runs down the centre column of lineart_post.render_ink(...):
    assert runs(render_ink(gray)) == 2                      # the bug: both edges traced
    assert runs(render_ink(fill_outlines(rgb, gray))) == 1  # one centre line

def test_stroke_fill_keeps_solid_disk_outline():
    # black disk radius 40 at 832 px: max dt 40 >= 13 -> solid
    out = fill_outlines(rgb, gray)
    assert np.array_equal(out[disk_mask], gray[disk_mask])  # interior untouched, xdog outline kept

def test_stroke_fill_fills_junction_blob():
    # black disk radius 12: survives the r=10 opening, max dt about 12 < 13 -> filled
    assert (fill_outlines(rgb, gray)[disk_mask] == 0).all()

def test_stroke_fill_matches_experiment():
    # experiments/samples/character_ref.png; import experiments.single_outline (repo root on sys.path)
    assert np.array_equal(stroke_fill(rgb, gray, 70, 10, 13), single_outline.stroke_fill(rgb, gray, 70, 10, 13))

def test_stroke_fill_constants_snapshot():
    msg = "stroke_fill 參數改了：請把 OUTLINE_FILL_VERSION 加 1，再更新這個快照"
    assert (STROKE_FILL_LUMA, STROKE_FILL_RADIUS, STROKE_FILL_KEEP, STROKE_FILL_REF_WIDTH, OUTLINE_FILL_VERSION) == (70, 10, 13.0, 832, 1), msg

# tests/test_lineart_render.py
def test_render_lineart_outline_fill_off_is_default():   # same pixels as calling without the kwarg
def test_render_lineart_outline_fill_on():               # == render_ink(fill_outlines(img, xdog)) and != default on the band image

# tests/test_page_lineart.py
def test_lineart_key_unchanged_since_before_structure_cleanup():
    # existing assertion, plus: page with outline_fill=False -> still "33f670f493f81344"
    # page with outline_fill=True -> a different, pinned literal (compute once with the new code, paste it, freeze it)
def test_outline_fill_page_renders_with_fill(env, monkeypatch):
    # spy on page_lineart.render_lineart; set page.outline_fill=True
    # -> ensure_page_lineart passes outline_fill=True, and its key != the key with outline_fill=False

# tests/test_pipeline.py
def test_new_composite_sets_outline_fill(client, app):
    # book_page + drain -> page composed; force outline_fill=False in the DB (legacy page)
    # POST a new pose (?oneclick=true), drain -> outline_fill is True
def test_reused_composite_keeps_outline_fill_and_key(client, app):   # Review Focus 3
    # composed page with outline_fill forced False; remember lineart_key
    # enqueue_page_task + drain (same inputs: reuses the composite) -> outline_fill False, lineart_key unchanged
```

- [ ] **Step 2: Run the four test files. Expected: FAIL** (no `stroke_fill`, no keyword argument, the flag is never set).

- [ ] **Step 3: Implement** the interfaces above.

- [ ] **Step 4: Run the four test files and `tests/test_pipeline_byte_identity.py`. Expected: PASS**, with the byte-identity file unchanged.

- [ ] **Step 5: Commit** with `feat(lineart): stroke_fill single outline for new/redrawn pages (outline_fill)`.

---

### Task 3: Prompt outline phrase constant (#8, code part)

**Files:**
- Modify: `backend/app/services/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Produces: `OUTLINE_PHRASE = "clean thin black outlines"`. Build `QWEN_CHARACTER_TEMPLATE`, `QWEN_POSE_TEMPLATE` and `QWEN_BACKGROUND_TEMPLATE` with string concatenation, putting `OUTLINE_PHRASE` where each template now has `thick clean black outlines`. The templates still use `.format`, so the phrase must not contain braces. Add a comment saying the final wording comes from the Task 16 GPU calibration (spec §4.8).

- [ ] **Step 1: Update the three pinned template strings** in `tests/test_prompts.py` (around lines 33, 100 and 163) to `clean thin black outlines`, and add:

```python
def test_outline_phrase_is_the_only_outline_wording():
    assert OUTLINE_PHRASE == "clean thin black outlines"
    for t in (QWEN_CHARACTER_TEMPLATE, QWEN_POSE_TEMPLATE, QWEN_BACKGROUND_TEMPLATE):
        assert t.count(OUTLINE_PHRASE) == 1 and "thick" not in t
```

- [ ] **Step 2: Run `tests/test_prompts.py`. Expected: FAIL.**
- [ ] **Step 3: Implement the constant.**
- [ ] **Step 4: Run `tests/test_prompts.py`, `tests/test_api_books.py` and `tests/test_api_compose.py`. Expected: PASS.**
- [ ] **Step 5: Commit** with `feat(prompts): OUTLINE_PHRASE constant, candidate wording 'clean thin black outlines'`.

---

### Task 4: Delete a book, backend (#1)

**Files:**
- Create: `backend/app/services/book_delete.py`
- Modify:
  - `backend/app/services/auth.py`: extract the shared cancel;
  - `backend/app/api/books.py`: new `DELETE`, plus `BookOut.mine` wiring;
  - `backend/app/api/schemas.py`: `BookOut.mine`;
  - `backend/app/services/worker.py`: drop objects whose row vanished;
  - `scripts/storage_gc.py`: `max_book` from the sequence;
  - `scripts/invites.py`: `delete-data` reuses the row deletion.
- Test: create `tests/test_book_delete.py`; modify `tests/test_ownership.py` and `tests/test_scripts.py`.

**Interfaces:**
- Produces in `auth.py`: `cancel_queued_in_books(session: Session, book_ids, *, reason: str) -> int`. `book_ids` is a SQL `select(Book.id)…`. It holds the body of today's `cancel_owner_queue` (QUEUED generations → CANCELLED with a refund, QUEUED PageTasks → FAILED). `cancel_owner_queue` becomes a one-line call to it. It does not commit.
- Produces in `book_delete.py`:
  - `BOOK_DELETED = "繪本已刪除"`.
  - `delete_books_rows(session: Session, book_ids: list[int]) -> None`. It does not commit. It calls `cancel_queued_in_books(…, reason=BOOK_DELETED)`, then deletes LineartEdit (by page ids or generation ids), PageTask, PageHistory, Generation, Page and Book rows, in that order (the order `scripts/invites.py` already uses). `invites.py delete-data` calls it in place of its inline deletes.
- Produces in `books.py`: `DELETE /api/books/{book_id}` → 204.
  - It loads the book with `owned_book`. If `book.owner_id != owner.id` it returns 404 `BOOK_NOT_FOUND`, because an admin may delete only their own books.
  - It calls `delete_books_rows(session, [book_id])`, then `commit`.
  - After the commit it calls `storage.delete_prefix(f"books/{book_id}/")` inside `try/except Exception`, which logs a warning and still returns 204.
  - It does **not** refuse a RUNNING generation or page task.
- Produces in `schemas.py`: `BookOut.mine: bool`. `book_outs(session, books, viewer: Owner | None = None)` and `book_out(session, book, viewer=None)` set `mine = viewer is None or b.owner_id == viewer.id`. `list_books` and `get_book` pass `owner`.
- Produces in `worker.py`: when `_finish` returns False inside `write_success`, call `self._drop_if_gone(gen_id, [rel, mask_rel])`. If `session.get(Generation, gen_id) is None`, it deletes those keys (best effort, logged).
- Produces in `storage_gc.py`: `max_book=_seq(s, Book)`. With AUTOINCREMENT this lets GC collect objects left behind by the highest-numbered deleted book.

- [ ] **Step 1: Write the failing tests** (`tests/test_book_delete.py`, using `book_page`/`drain` from `tests/test_pipeline.py`)

```python
def test_delete_book_removes_rows_and_storage(client, app):
    # composed book (book_page + drain); DELETE /api/books/{id} -> 204
    # GET /api/books/{id} -> 404; no Page/Generation/PageTask/PageHistory/LineartEdit rows for it
    # list(storage.list(f"books/{id}/")) == []

def test_delete_book_cancels_and_refunds_queued(client, app):
    # queue a pose (do not run); usage before = n; DELETE -> today's Usage.generations == n - 1

def test_delete_book_leaves_other_books_untouched(client, app):
    # two books; delete one -> the other's pages, generations and objects are all still there

def test_admin_cannot_delete_another_owners_book(world):   # Review Focus 1 (world fixture as in tests/test_sharing.py)
    # admin session DELETE on a member's book -> 404 "找不到這本繪本"; the book and its rows still exist

def test_delete_book_while_generation_running(client, app, monkeypatch):
    # wrap app.state.engine.generate: delete_books_rows + commit in a fresh session, then delegate
    # run_once returns True, raises nothing; storage.list(f"books/{id}/") == []

def test_share_link_404_after_delete(world):
    # shared book; owner deletes it -> GET /api/shared/books/{id} -> 404

def test_book_ids_never_reused(client):
    # create, delete, create -> new id > deleted id

def test_book_out_mine(world):
    # admin GET /api/books lists a member's book with mine=False and its own with mine=True
```

In `tests/test_scripts.py`: `test_gc_collects_objects_of_deleted_highest_book`. Put an object under `books/{max_id}/generations/…`, delete that book's rows, run gc → the object is classified as an orphan (not `mismatched`) and deleted.

In `tests/test_ownership.py`: add `FIX_ROUND_ROUTES = {("DELETE", "/api/books/{book_id}")}` and assert `FIX_ROUND_ROUTES <= set(ID_ROUTES)`. Tasks 5 and 7 add their routes to this set.

- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run `tests/test_book_delete.py`, `tests/test_ownership.py`, `tests/test_scripts.py`, `tests/test_worker.py`, `tests/test_admin_invites.py` and `tests/test_api_books.py`. Expected: PASS.**
- [ ] **Step 5: Regenerate the API types** (`npm run gen:api`) and run `npx tsc --noEmit`. Add `mine: true` to any TS fixture that builds a `Book`.
- [ ] **Step 6: Commit** with `feat(api): DELETE /books/{id} with storage cleanup; BookOut.mine`.

---

### Task 5: Delete a character candidate, backend (#2)

**Files:**
- Modify: `backend/app/api/books.py`
- Test: `tests/test_api_books.py`, `tests/test_ownership.py`

**Interfaces:**
- Produces: `DELETE /api/books/{book_id}/character/candidates/{gen_id}` → 204. It marks the row `discarded=True` and sets `discarded_at=coalesce(discarded_at, now)`, using the same conditional-UPDATE pattern as `PATCH /generations/{id}`.
  - 404 「找不到這張候選圖」 if the generation is not a CHARACTER generation of this book.
  - 409 「選好的主角不能刪」 if `gen_id == book.character_generation_id`.
  - 409 「這張候選還在畫，請先取消」 if the status is QUEUED or RUNNING.
  - The undo path is the existing `PATCH /api/generations/{id} {"discarded": false}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_delete_candidate_discards_it(client, app):
    # two succeeded candidates, choose #1; DELETE #2 -> 204
    # row discarded True and discarded_at set; GET generations?kind=character no longer lists it
    # PATCH /api/generations/{id2} {"discarded": false} -> it is back

def test_delete_chosen_candidate_is_409(client, app):          # 409 "選好的主角不能刪"
def test_delete_active_candidate_is_409(client, app):          # QUEUED -> 409 "這張候選還在畫，請先取消"
def test_delete_candidate_wrong_kind_or_book_is_404(client, app)  # a pose id, or another book's candidate
```

Add `("DELETE", "/api/books/{book_id}/character/candidates/{gen_id}")` to `FIX_ROUND_ROUTES`.

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement, run them again until they pass, and run `npm run gen:api`.
- [ ] **Step 3: Commit** with `feat(api): delete unwanted character candidates (discard)`.

---

### Task 6: Soft `review_pose` tag and progress in pages (#3, and the #4 semantics)

**Files:**
- Modify:
  - `backend/app/services/page_state.py`;
  - `backend/app/api/produce.py`: `produce`, `book_overview`;
  - `backend/app/api/schemas.py`;
  - `backend/app/api/export.py`: `_included`, `export_summary`;
  - `backend/app/services/pipeline.py`: `_attempt` step 5;
  - `backend/app/api/compose.py`: `_pick`.
- Test: `tests/test_api_produce.py`, `tests/test_api_export.py`, `tests/test_pipeline.py`, `tests/test_api_compose.py`, `tests/test_api_history.py`, `tests/test_api_books.py`

**Interfaces:**
- Produces in `page_state.py`:
  - `ATTENTION_REVIEW_POSE = "review_pose"`.
  - `REVIEW_POSE_DETAIL = "請看一下：換了新的主角動作"`.
  - `hard_attention(attention: str | None) -> bool` is `attention is not None and attention != ATTENTION_REVIEW_POSE`.
  - `page_state` uses `hard_attention(page.attention)` in place of `page.attention`, so a PASSED, not-busy `review_pose` page is `"ready"`.
- Produces in `schemas.py`:
  - `class ProgressPages(BaseModel): ready: int; total: int`.
  - `BookOverviewOut.progress_pages: ProgressPages`.
  - `OverviewPageOut.review: bool`, true when `attention == "review_pose"`. Its `attention_detail` is filled for review pages too.
  - Keep `progress` as it is.
- Behaviour:
  - `book_overview`: `progress_pages.total = len(pages)` and `ready = count(state == "ready")`.
  - `produce`: the pipeline-pages condition uses `hard_attention(p.attention)` in place of `p.attention is not None`.
  - `export._included` and `export_summary.ready_ids`: a page is ready when `not hard_attention(p.attention)`.
  - Pipeline step 5: `attention=case((col(Page.attention) == ATTENTION_REVIEW_POSE, ATTENTION_REVIEW_POSE), else_=None)`, and the same for `attention_detail`. The pipeline keeps the soft tag; it still clears `failed`.
  - `_pick`, when `oneclick and kind == GenKind.POSE`: the same `UPDATE` clears a `review_pose` attention and detail. Background picks leave it alone.
  - `accept` already clears it.

**Ruling:** the spec says the tag "is set when the pipeline finishes". This plan sets it when the take lands (Task 7) and has the pipeline *preserve* it. The page is busy (製作中) until the pipeline finishes either way, so what the user sees is identical, and a later pipeline rerun (paper change, 繼續做) cannot re-tag a page the user already accepted.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_produce.py
def test_progress_pages_counts_ready_pages_only(client, app):
    # 5 pages: PASSED; PASSED+review_pose; PASSED+failed; DRAFT; PASSED with a queued pose (busy)
    assert overview["progress_pages"] == {"ready": 2, "total": 5}

def test_progress_pages_never_exceeds_total(client, app):
    # full run, then several C edits (oneclick placement saves + drain)
    assert pp["ready"] <= pp["total"] == len(pages)

def test_review_pose_page_is_ready_and_flagged(client, app):
    # page: state "ready", review True, attention_detail "請看一下：換了新的主角動作"

def test_produce_does_not_requeue_review_pose_page(client, app):
    # PASSED + review_pose, both images -> POST /produce: pipeline_pages == []

# tests/test_api_export.py
def test_review_pose_page_in_default_export(client, app):
    # summary ready_ids includes it; export.pdf?include=ready includes the page

# tests/test_pipeline.py
def test_pipeline_keeps_review_pose(client, app):   # set review_pose, enqueue + drain -> PASSED, attention still "review_pose"

# tests/test_api_compose.py
def test_oneclick_pose_pick_clears_review_pose(client, app)
def test_oneclick_background_pick_keeps_review_pose(client, app)

# tests/test_api_history.py
def test_accept_clears_review_pose(client, app)

# tests/test_api_books.py
def test_book_stats_count_review_pose_as_ready(client, app)   # stats.ready includes it, stats.attention does not
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement and run them again until they pass.
- [ ] **Step 3: Run `npm run gen:api`.** Add `review: false` to the overview fixtures and `progress_pages` to `ov()` if tsc requires them.
- [ ] **Step 4: Commit** with `feat: soft review_pose tag; overview progress_pages`.

---

### Task 7: Pose redraw, backend (#4A batch and #4B inline count)

**Files:**
- Modify:
  - `backend/app/services/gen_request.py`: flag names;
  - `backend/app/services/slots.py`: `not_candidate`;
  - `backend/app/services/jobs.py`: `redraw_jobs`, `BookState.has_both_images`;
  - `backend/app/services/pipeline.py`: `on_generation_succeeded`;
  - `backend/app/api/schemas.py`;
  - `backend/app/api/compose.py`: `create_pose`, new endpoint.
- Test: create `tests/test_pose_redraw.py`; modify `tests/test_ownership.py`.

**Interfaces:**
- Consumes: `ATTENTION_REVIEW_POSE` and `REVIEW_POSE_DETAIL` (Task 6).
- Produces in `gen_request.py`: `CANDIDATE = "candidate"` and `REVIEW = "review"`, extra keys documented in the module docstring next to `TAKE`.
- Produces in `pipeline.py`:
  - `is_candidate(gen) -> bool` and `wants_review(gen) -> bool`. Both reuse the JSON parsing of `wants_take`; refactor it into `_flag(gen, key) -> bool`.
  - In `on_generation_succeeded`: a candidate returns False immediately (no selection, no page task).
  - After a successful `take_generation` of a gen with REVIEW, the same transaction sets `attention=ATTENTION_REVIEW_POSE` and `attention_detail=REVIEW_POSE_DETAIL`.
- Produces in `slots.py`: `not_candidate()`, a SQL condition that is true unless `json_valid(request_json)` and `json_extract(request_json, '$.candidate')` is true (use `case`, so malformed JSON never raises). `newest_usable_ai` adds it, so neither auto-select nor 「還原成自動畫的那張」 picks a candidate.
- Produces in `jobs.py`:
  - `redraw_jobs(session, book, page, action: str, count: int, *, take_first: bool, review: bool) -> list[Job]`. It returns `count` pose jobs.
    - Job 1 gets `TAKE` if `take_first` and `REVIEW` if `review`.
    - Jobs 2..count get `CANDIDATE`.
    - Variations are distinct within the call: each pick is added to the exclusion list passed to `pick_pose_variation`.
  - `BookState.has_both_images` ignores candidate generations.
- Produces in `schemas.py`:
  - `PoseCreate.count: int = Field(1, ge=1, le=3)`.
  - `class PoseRedrawIn(BaseModel): page_ids: list[DbId] = Field(min_length=1, max_length=500); count: int = Field(1, ge=1, le=3)`.
  - `class PoseRedrawOut(BaseModel): queued: list[GenerationOut]; skipped: list[BatchSkip]`.
- Produces in `compose.py`:
  - `create_pose` uses `redraw_jobs(…, body.count, take_first=oneclick, review=False)` and still returns the first `GenerationOut`. With `count=1` the request is identical to today's 換一張.
  - New `POST /api/books/{book_id}/poses/redraw` → 202 `PoseRedrawOut`:
    - runs under `BATCH_LOCK`;
    - returns 409 「請先設定角色」 without a character;
    - returns 400 「page_ids 必須是這本繪本的頁面」 for foreign ids;
    - dedupes the ids, keeping their order;
    - skips pages whose `action_prompt` is blank (`BatchSkip(kind="pose", reason="沒有動作描述")`);
    - queues `redraw_jobs(…, page.action_prompt, count, take_first=True, review=True)` for each remaining page through **one** `enqueue_jobs` call, so a cap shortfall is a 429 for the whole batch.

- [ ] **Step 1: Write the failing tests** (`tests/test_pose_redraw.py`, using `book_page`/`drain`)

```python
def test_batch_redraw_queues_take_plus_candidates(client, app):
    # 2 composed pages, count 3 -> 202, 6 queued
    # per page: first request_json take True and review True; the other two candidate True, no take
    # the three variations of one page are all different

def test_batch_redraw_over_cap_is_429_and_queues_nothing(client, app):   # Review Focus 2
    # owner cap leaves 5; 2 pages x 3 -> 429 detail "今天只剩 5 次，這次需要 6 次"
    # no new generations; usage unchanged; attention unchanged

def test_batch_redraw_end_to_end(client, app):
    # count 2, drain -> each page: pose_generation_id == its take, status PASSED,
    #   attention "review_pose", overview state "ready" and review True
    # the candidate is SUCCEEDED but not selected; POST /pages/{id}/undo -> previous pose back

def test_two_redraws_keep_every_pose(client, app):   # Review Focus 4
    # batch count 1 twice before draining; drain
    # page pose == second take; GET generations?kind=pose&page_id lists original + both takes
    # undo -> first take; undo again -> original

def test_candidate_success_selects_nothing(client, app):
    # on_generation_succeeded(candidate) is False; page slots unchanged; no PageTask queued

def test_candidates_never_auto_selected_or_restored(client, app):
    # newest_usable_ai skips the candidate; POST restore pose -> picks the take, not the newer candidate

def test_batch_redraw_validation(client, app):
    # count 0 or 4 -> 422; foreign page id -> 400; no character -> 409; blank action -> skipped

def test_inline_count_two(client, app):
    # POST /pages/{id}/poses?oneclick=true {action, count: 2} -> 2 gens: take (no review) + candidate
    # after drain: attention is None, pose == the take
```

Add `("POST", "/api/books/{book_id}/poses/redraw")` to `FIX_ROUND_ROUTES`.

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement, run them until they pass, and run `tests/test_pipeline.py` and `tests/test_api_compose.py` as well.
- [ ] **Step 3: Run `npm run gen:api`.**
- [ ] **Step 4: Commit** with `feat(api): batch and multi-count pose redraw (take + candidates, review_pose)`.

---

### Task 8: Setup edits mark pages 要重畫, and /produce redraws them (#7, backend)

**Files:**
- Modify:
  - `backend/app/api/pages.py`: `update_page`;
  - `backend/app/api/schemas.py`: `PageOut`, `BookOverviewOut`;
  - `backend/app/services/jobs.py`: `BookState`, `build_job`, new helper;
  - `backend/app/api/produce.py`.
- Test: create `tests/test_setup_redraw.py`.

**Interfaces:**
- Consumes: `Page.redraw_background` and `Page.redraw_pose` (Task 1); `TAKE`, `REVIEW` and the review-tag-on-take behaviour (Task 7).
- Produces in `pages.py`: `PATCH /api/pages/{page_id}?from_setup=true` takes the same `PageUpdate` body. With `from_setup` only, and before the fields are applied:
  - The scene changed, `page.background_generation_id` is set, and that generation's `engine != UPLOAD_ENGINE`: set `redraw_background=True`.
  - The action changed and `page.pose_generation_id` is set: set `redraw_pose=True`.
  - Caption fields never set a flag. Without `from_setup`, nothing changes, including C's `?oneclick=true` path.
- Produces in `schemas.py`: `PageOut.redraw_background: bool`, `PageOut.redraw_pose: bool` and `BookOverviewOut.redraw_count: int`, the number of pages with either flag.
- Produces in `jobs.py`:
  - `BookState.skip_reason(page, kind, *, redraw: bool = False)` and `BookState.plan(has_character, upload_pages=frozenset(), *, redraw: bool = False)`. With `redraw=True`, a page flagged for that kind is planned (reason None) unless a generation of that kind is QUEUED or RUNNING. In that case the reason is the existing 「…產生中」 and the flag is kept.
  - `build_job(session, book, page, kind, *, redraw: bool = False)`. When `redraw` and the page is flagged for `kind`, it returns `take(job, True)`, and a POSE also gets `REVIEW`.
  - `clear_redraw_flag(session, page_id: int, kind: GenKind) -> None` runs an UPDATE without a commit.
- Produces in `produce.py`:
  - `produce_plan` and `produce` call `_needed(…, redraw=True)` and `build_job(…, redraw=True)`.
  - After `add_queued` for each granted job, `produce` calls `clear_redraw_flag` in the same transaction. Deferred ones keep their flag.
  - `book_overview.deferred_count` uses `_needed(…, redraw=False)`, so flagged pages are not counted as "明天繼續".
  - `redraw_count` comes from the page flags.
- The materials-batch endpoint is unchanged (`redraw=False`).

**Ruling:** a flagged background is also redrawn as a take, so it replaces a user-picked AI background, but it gets no `review_pose` tag. Only a pose redraw gets the tag (spec §4.7 names only the pose).

- [ ] **Step 1: Write the failing tests**

```python
def test_setup_scene_edit_flags_background(client, app)       # AI background -> redraw_background True, redraw_pose False
def test_setup_action_edit_flags_pose(client, app)
def test_setup_edit_on_page_without_art_sets_no_flag(client, app)
def test_setup_scene_edit_on_uploaded_background_saves_text_only(client, app)   # scene saved, no flag
def test_setup_caption_edit_sets_no_flag(client, app)
def test_c_and_plain_patch_never_flag(client, app)            # ?oneclick=true and no query param: action change -> no flag

def test_produce_redraws_only_flagged_pages(client, app):
    # 3 ready pages; setup action edit on page 2 -> plan poses == 1, backgrounds == 0
    # produce: queued == [one pose for page 2] with take True and review True; page 2 redraw_pose is False now
    # drain -> page 2 pose changed and attention "review_pose"; pages 1 and 3 have the same generation ids as before

def test_produce_flagged_background_is_take_without_review(client, app)
def test_overview_redraw_count_not_deferred(client, app)      # redraw_count 1, deferred_count 0
def test_flag_kept_when_cap_exhausted(client, app)            # remaining 0 -> 429, redraw_pose still True
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement and run them again until they pass. Also run `tests/test_api_produce.py`, `tests/test_api_pages.py` and `tests/test_api_compose.py`.
- [ ] **Step 3: Run `npm run gen:api`**, then add `redraw_background: false`, `redraw_pose: false` and `redraw_count: 0` to the TS fixtures.
- [ ] **Step 4: Commit** with `feat(api): setup edits flag pages for redraw; /produce redraws them`.

---

### Task 9: Bookshelf 「⋯」 menu and delete confirm (#1, frontend)

**Files:**
- Create: `frontend/src/features/books/BookCardMenu.tsx` and `BookCardMenu.module.css`, `frontend/src/features/books/BookCard.test.tsx`, `frontend/e2e/delete-book.spec.ts`
- Modify:
  - `frontend/src/features/books/BookCard.tsx` and `.module.css`. The card's `<Link>` and the menu button become siblings inside a wrapper, because interactive elements must not be nested.
  - `frontend/src/api/hooks.ts`
  - `frontend/src/components/ui/Button.tsx`, `Button.module.css` and `Button.test.tsx`: add a filled `danger` variant using `--danger` tokens.

**Interfaces:**
- Consumes: `DELETE /api/books/{id}` and `Book.mine` (Task 4).
- Produces:
  - `useDeleteBook(): UseMutationResult<void, unknown, Book>`. On success it removes the book from the `qk.books()` cache, removes the `qk.book(id)` and `qk.overview(id)` queries, and invalidates `qk.sharedBooks()`.
  - `BookCardMenu({ book }: { book: Book })` renders only when `book.mine`:
    - a button 「⋯」 with `aria-label={`「${book.title}」的選單`}` and `aria-haspopup="menu"`;
    - `role="menu"` with one `menuitem`, 「刪除這本書」. The card has no rename or 主角與故事 entries today (spec §6). Esc or a click outside closes the menu.
- Confirm `Dialog`:
  - title 「確定要刪除「{title}」嗎？」;
  - body 「{n} 頁、所有圖和修過的線都會刪掉，刪了就救不回來。分享出去的連結也會失效。」 with `n = book.stats.pages`;
  - buttons 「取消」 and 「刪除」 (`variant="danger"`).
- Success toast 「已刪除「{title}」」 (success tone). Error toast `errMsg(e, "沒辦法刪除這本書，請稍後再試")`.

**Ruling:** the decision table writes the title as 『{title}』 because it is nested inside the table's own 「」. The approved mockup C and the toast copy in §4.1 both use 「」, so the dialog title is 「確定要刪除「{title}」嗎？」.

- [ ] **Step 1: Write the failing tests** (`BookCard.test.tsx`)

```tsx
it("shows the ⋯ menu only on own books")                 // mine=false -> no button named 「小狐狸的一天」的選單
it("confirm dialog has the exact copy")                    // title 確定要刪除「小狐狸的一天」嗎？, body with 20 頁, buttons 取消 / 刪除
it("取消 closes without a request")
it("刪除 sends DELETE and drops the card")                 // fetch DELETE /api/books/1; card gone; toast 已刪除「小狐狸的一天」
it("a failed delete keeps the card and shows the error toast")
```

e2e `delete-book.spec.ts`: seed a book with `createBook`, open `/`, open 「⋯」, choose 刪除這本書, then 刪除. The card disappears, `GET /api/books/{id}` returns 404, and a reload still shows no card.

- [ ] **Step 2: Run the tests and confirm they fail.** Implement, then run `npm test`, lint, tsc and the new e2e spec.
- [ ] **Step 3: Commit** with `feat(web): delete a book from the bookshelf`.

---

### Task 10: Character candidate 「×」 with undo toast (#2, frontend)

**Files:**
- Modify: `frontend/src/components/ui/Toast.tsx`, `Toast.module.css` and `Toast.test.tsx`; `frontend/src/features/setup/CharacterSection.tsx`; `frontend/src/features/setup/SetupPage.module.css`; `frontend/src/api/hooks.ts`
- Test: create `frontend/src/features/setup/CharacterSection.test.tsx`

**Interfaces:**
- Consumes: `DELETE /api/books/{book_id}/character/candidates/{gen_id}` (Task 5) and `PATCH /api/generations/{id}`.
- Produces:
  - `ToastApi.show(message: string, tone?: ToastTone, action?: { label: string; onClick: () => void })`. With an action, the toast renders a button. Clicking it calls `onClick` and dismisses the toast. Existing callers are unchanged.
  - `useDeleteCandidate(bookId: number)` → DELETE, then invalidates `qk.generationsOf(bookId)`.
  - `Candidate` shows a 「×」 button (`aria-label={`刪掉候選 ${n}`}`) only when `!on && !isActive(g)`. It is shown on failed candidates too.
  - On success the toast reads 「已刪掉這張候選」 (info) with the action 「復原」, which calls `PATCH /generations/{id} {discarded:false}` and invalidates.

**Ruling:** the spec gives no toast text. 「已刪掉這張候選」 and 「復原」 follow mockup C's note (「可在提示裡「復原」一次」).

- [ ] **Step 1: Write the failing tests**

```tsx
// Toast.test.tsx
it("an action toast shows the button, calls it once and dismisses")
// CharacterSection.test.tsx
it("the chosen candidate has no ×, others do")             // queued candidate: no × either
it("× sends DELETE and the undo toast restores")           // DELETE .../character/candidates/12, then PATCH /api/generations/12 {discarded:false}
```

- [ ] **Step 2: Run the tests and confirm they fail.** Implement, then run the frontend checks.
- [ ] **Step 3: Commit** with `feat(web): remove unwanted character candidates (×, undo)`.

---

### Task 11: Copy-prompt fallback (#6)

**Files:**
- Create: `frontend/src/lib/copyText.ts`, `frontend/src/lib/copyText.test.ts`, `frontend/src/features/setup/CopyPromptDialog.tsx`, `frontend/e2e/copy-prompt.spec.ts`
- Modify:
  - `frontend/src/features/setup/StorySection.tsx` and `StorySection.test.tsx`;
  - `frontend/src/features/setup/SetupPage.module.css`;
  - `frontend/src/features/admin/InviteAdmin.tsx` and `InviteAdmin.test.tsx`.

**Interfaces:**
- Produces `copyText(text: string): Promise<boolean>`:
  - If `window.isSecureContext && navigator.clipboard?.writeText`, try it and return true when it resolves.
  - Otherwise, or if it rejects, append a hidden read-only `<textarea>`, select it, and return the result of `document.execCommand("copy")`. A throw counts as false. Always remove the textarea and restore focus to the previously focused element.
- Produces `CopyPromptDialog({ open, text, onClose })`:
  - a `Dialog` titled 「複製給 AI 的指令」;
  - a read-only `<textarea>` (`aria-label="給 AI 的指令"`) holding `text`, selected in a `useEffect` when it opens;
  - the hint 「已全選，按 Ctrl＋C（手機長按）複製」.
- StorySection: `copyText(AI_PROMPT(pasted || existing || 10))`.
  - On true: the existing toast 「已複製，貼到 ChatGPT 或 Claude，再接上你的故事」.
  - On false: open the dialog. The old toast 「無法複製，請手動選取」 is removed.
- InviteAdmin `CopyLink` uses `copyText`. On false it returns, and the link stays shown next to the button as today.

- [ ] **Step 1: Write the failing tests**

```ts
// copyText.test.ts
it("secure + clipboard ok -> true, no execCommand")
it("secure + clipboard rejects -> falls back to execCommand('copy')")
it("insecure -> never touches navigator.clipboard")
it("execCommand false or throwing -> false, textarea removed, focus restored")
// StorySection.test.tsx
it("copy fails -> dialog with the whole prompt selected")  // value === AI_PROMPT(10); selectionStart 0, selectionEnd === length; hint text exact
it("copy ok -> success toast, no dialog")
// InviteAdmin.test.tsx
it("copy failure keeps the link visible and shows no 已複製")
```

e2e `copy-prompt.spec.ts` (`localhost` counts as a secure origin, so simulate an insecure one): `page.addInitScript` that defines `window.isSecureContext = false` and makes `document.execCommand` return false. Open the setup page of a seeded book, click 「複製給 AI 的指令」, and the dialog shows a textarea whose value contains 「第1頁」 (from the prompt) and the hint text.

- [ ] **Step 2: Run the tests and confirm they fail.** Implement, then run the frontend checks and the e2e spec.
- [ ] **Step 3: Commit** with `fix(web): copy prompt falls back to execCommand, then a select-all dialog`.

---

### Task 12: B progress in pages and soft 請看一下 display (#3, frontend)

**Files:**
- Modify:
  - `frontend/src/features/book/overviewState.ts` and `overviewState.test.ts`;
  - `frontend/src/features/book/BookOverview.tsx`, `BookOverview.test.tsx` and `.module.css`;
  - `frontend/src/features/fix/fixState.ts` and `fixState.test.ts`;
  - `frontend/src/features/fix/FixPage.tsx` and `FixPage.test.tsx`.

**Interfaces:**
- Consumes: `progress_pages`, `OverviewPage.review` and `redraw_count` (Tasks 6 and 8).
- Produces in `overviewState.ts`:
  - `progressPages(o: BookOverview): { ready: number; total: number }` returns `o.progress_pages`. Delete `progressOf` and its test.
  - `inAttention(p: OverviewPage): boolean` is `p.state === "attention" || p.review`. `filterPages("attention")` and `countPages` use it, so a review page counts under both 可以印 and 請看一下.
  - `softNote(p)` returns `p.attention_detail` when `p.review`.
  - `CardMode` gains `"redraw"`. After `running`, if `o.redraw_count > 0`, the mode is `"redraw"`.
- `StatusCard`:
  - Running: the progress bar uses `ready/total`. The line reads 「做好 {ready} / {total} 頁」 plus `・${formatRemaining(estimate_s)}` when `estimate_s > 0` (that is, 「・剩約 N 分鐘」), followed by the existing 「可以先去忙，好了會通知你」.
  - Other modes: the line 「做好 {ready} / {total} 頁」 comes first; `countsLine` follows unless `ready === total`.
  - `"redraw"` mode: the heading is 「還有 {n} 頁要重畫」 and the action is the 「繼續做」 produce button.
  - 「去看那一頁 →」 uses the first `inAttention` page.
- PageCard: a review page shows the pill 「請看一下」 with the `attention` style and the note `attention_detail`. Its `href` keeps the filter behaviour.
- `fixState.pageState`: only `page.attention && page.attention !== "review_pose"` gives `"attention"`. `neighbours` with `filter="attention"` uses `inAttention`.
- FixPage: when `page.attention === "review_pose"`, show `<p className={styles.rowWarn}>{page.attention_detail}</p>` above the items list.

**Ruling:**
- Mockup A shows 「做好 20 / 20 頁」 on the finished card, so the page line shows in every mode, not only while running.
- The spec has no copy for the 要重畫 card state. It uses 「還有 {n} 頁要重畫」, matching the 「要重畫」 row label from §4.7. Without it, a fully printed book with one flagged page would show no 「繼續做」 button.

- [ ] **Step 1: Write the failing tests**

```ts
// overviewState.test.ts
it("progressPages reads progress_pages")
it("inAttention: review pages are in both 請看一下 and 可以印")
it("cardMode redraw when redraw_count > 0 and nothing is running")
// BookOverview.test.tsx
it("running: 做好 1 / 3 頁・剩約 2 分鐘")                  // estimate_s 90
it("done: 做好 3 / 3 頁")
it("review page card shows 請看一下 and 請看一下：換了新的主角動作")
it("redraw mode: 還有 1 頁要重畫 and 繼續做 posts /produce")
// fixState.test.ts
it("pageState: review_pose page is ready")
it("neighbours with filter attention steps through review pages")
// FixPage.test.tsx
it("review_pose shows the detail note and still offers 這頁可以印了 ✓")
```

- [ ] **Step 2: Run the tests and confirm they fail.** Implement, then run the frontend checks.
- [ ] **Step 3: Commit** with `feat(web): progress in pages; review_pose shown as a soft 請看一下`.

---

### Task 13: B 「選取多頁」 mode and redraw bar (#4A, frontend)

**Files:**
- Create: `frontend/src/features/book/RedrawBar.tsx` and `RedrawBar.module.css`
- Modify: `frontend/src/features/book/BookOverview.tsx`, `BookOverview.module.css` and `BookOverview.test.tsx`; `frontend/src/api/hooks.ts`

**Interfaces:**
- Consumes: `POST /api/books/{id}/poses/redraw` (Task 7).
- Produces:
  - `useRedrawPoses(bookId): UseMutationResult<PoseRedrawOut, unknown, { pageIds: number[]; count: 1 | 2 | 3 }>`. On success it invalidates the overview, pages, generations, queue, books and producePlan queries.
  - `RedrawBar({ selected: number, count, onCount, onSubmit, onCancel, busy })`. The layout follows mockup A:
    - 「已選 {N} 頁」;
    - 「每頁重畫主角動作，背景和字不動」;
    - a radio group (`aria-label="每頁畫幾張"`) laid out as 「每頁畫 [1][2][3] 張讓我挑」, default 1;
    - 「會畫 {N×count} 張」;
    - the primary button 「重畫 {N} 頁的主角」, disabled when N is 0;
    - 「取消」.
- BookOverview:
  - A header toggle button 「選取多頁」 (`aria-pressed`; the label is 「✓ 選取多頁」 when on). While on, the filter row shows the hint 「點卡片勾選要重畫主角的頁」.
  - Each card renders a checkbox (`aria-label={`選第 ${n} 頁`}`), and clicking the card toggles it instead of navigating.
  - Submit sends the selected ids in page order. On success the mode turns off and the selection clears.
  - If `skipped.length > 0`, show the info toast 「{k} 頁沒有動作描述，沒有重畫」.
  - Errors go to `errMsg(e, "沒辦法重畫，請稍後再試")`; this shows the backend's 429 message too.

**Ruling:** the spec has no copy for skipped pages or errors. The two strings above are new. The bar's subtitle and hint come from mockup A. Mockup A's 「請挑一張」 note and its default of 2 are superseded (spec §6).

- [ ] **Step 1: Write the failing tests** (`BookOverview.test.tsx`)

```tsx
it("選取多頁 shows checkboxes; card click toggles instead of navigating")
it("bar: 已選 2 頁, default 1, 會畫 2 張; choosing 3 -> 會畫 6 張, button 重畫 2 頁的主角")
it("submit posts {page_ids in page order, count} and leaves select mode")
it("取消 clears the selection")
it("429 shows the backend message as an error toast")
```

- [ ] **Step 2: Run the tests and confirm they fail.** Implement, then run the frontend checks.
- [ ] **Step 3: Commit** with `feat(web): B select-many and batch pose redraw bar`.

---

### Task 14: C pose history row and inline redraw (#4B, frontend)

**Files:**
- Create: `frontend/src/features/fix/seenPoses.ts` and `seenPoses.test.ts`, `frontend/e2e/redraw-poses.spec.ts`
- Modify:
  - `frontend/src/features/fix/rows/PoseRow.tsx`, `frontend/src/features/fix/FixPage.tsx` (pass the page's generations to PoseRow) and `FixPage.module.css`;
  - `frontend/src/features/fix/fixApi.ts`;
  - `frontend/src/features/fix/FixPage.test.tsx` and `frontend/e2e/fix-page.spec.ts`, wherever they use C's pose 「換一張」 or 「改動作」.

**Interfaces:**
- Consumes: `PoseCreate.count` (Task 7) and the existing `POST /pages/{id}/pose?oneclick=true`.
- Produces in `fixApi.ts`:
  - `useRegenerate` accepts `{ kind: "pose"; action: string; count?: 1 | 2 | 3 }` and sends `count`.
  - `useSelectPoseC(bookId, pageId)` is a `usePageMutation<number>` that posts `/pages/{id}/pose?oneclick=true {generation_id}`.
- Produces in `seenPoses.ts`:
  - `lastSeenPose(pageId: number): number | null` reads `localStorage["cb:seenPose:" + pageId]`.
  - `markSeenPose(pageId: number, maxId: number): void`.
  - Both swallow storage exceptions; on failure `lastSeenPose` returns null.
- PoseRow (mockup B). The header buttons 「換一張」 and 「改動作」 are replaced by an always-visible panel:
  - The row subtitle is 「這頁畫過 {n} 張・點一下就換」, or 「正在畫新的一張…」 while a pose is active.
  - A list (`aria-label="畫過的角色動作"`) of every succeeded, non-discarded pose of this page, newest first.
    - Each entry is a button with `aria-pressed` set for the current one.
    - The current entry is tagged 「使用中」. An entry is tagged 「新」 when `id > lastSeen` and `lastSeen !== null`.
    - Clicking a non-current entry calls `useSelectPoseC`, which snapshots first, so ↶ 復原 restores the previous pose.
    - On mount, read `lastSeen`. After that, `markSeenPose(pageId, maxId)`.
  - The existing 動作描述 textarea, the 表情 and 面向 `ExclusiveChips`, and the hint stay unchanged.
  - A radio group laid out as 「一次畫 [1][2][3] 張」 (`aria-label="一次畫幾張"`, default 1).
  - The button 「重畫主角」. It has the same save-then-flush-then-regenerate sequence as today's `again()`, and it is disabled under the same conditions.

**Ruling:** mockup B labels the action box 「再畫幾張」, which looks like a mockup slip. The existing label 「動作描述」 stays. 「新」 is per browser, using a localStorage high-water mark (spec §6 allows this).

- [ ] **Step 1: Write the failing tests**

```ts
// seenPoses.test.ts
it("round-trips the high-water mark per page")
it("returns null and does not throw when localStorage throws")   // Review Focus 5
// FixPage.test.tsx
it("lists every pose newest first, current tagged 使用中")
it("poses newer than the last visit are tagged 新")
it("clicking a pose posts /pose?oneclick=true with its id")
it("重畫主角 with 一次畫 2 張 posts count 2 after saving the action")
it("renders without 新 badges when localStorage throws")        // Review Focus 5
```

e2e `redraw-poses.spec.ts` (fake backend):
1. Seed a book with 3 pages and run 開始做這本書 until every page is 可以印.
2. In B, press 選取多頁, tick the 3 cards, keep 1, and press 重畫 3 頁的主角.
3. Poll the overview until nothing is working. Each card shows 請看一下, and 「做好 3 / 3 頁」 is visible.
4. Open page 1 in C. The pose list has 2 entries, and the new one is 使用中.
5. Click the older entry, then ↶ 復原. The newer pose is current again.

In `fix-page.spec.ts`, replace the 「改動作」 expansion with the always-visible panel; the chips are visible without a click.

- [ ] **Step 2: Run the tests and confirm they fail.** Implement, then run the frontend checks and all of e2e.
- [ ] **Step 3: Commit** with `feat(web): C pose history row and inline redraw (1–3 at a time)`.

---

### Task 15: Edit existing pages in setup, frontend (#7)

**Files:**
- Create: `frontend/e2e/setup-edit.spec.ts`
- Modify:
  - `frontend/src/features/setup/StorySection.tsx` and `StorySection.test.tsx`;
  - `frontend/src/features/setup/storyPreview.ts` and `storyPreview.test.ts`;
  - `frontend/src/features/setup/SetupPage.tsx` and `SetupPage.module.css`;
  - `frontend/src/api/hooks.ts`.

**Interfaces:**
- Consumes: `PATCH /pages/{id}?from_setup=true` and `PageOut.redraw_background` / `PageOut.redraw_pose` (Task 8).
- Produces:
  - `useUpdatePageFromSetup(bookId)`: `api.patch<Page>(`/pages/${id}?from_setup=true`, body)` with only the changed fields. It invalidates pages, overview, producePlan and books.
  - `PageMaterials` gains `redrawBackground: boolean` and `redrawPose: boolean`, read from the page fields.
  - `needsBackground` and `needsPose` are also true when the matching flag is set, so 「會畫幾張圖」 counts them.
  - `StorySection` gets a new prop `onSavePage(pageId, patch: { scene_prompt?, action_prompt?, caption_zh?, caption_en? }) => Promise<void>`.
- A `StoryPreviewRow` for an existing page (`pageId != null && draft == null`) shows a 「編輯」 button.
  - Pressing it shows inline inputs 場景, 動作, 中文 and 英文, capped at `MAX_SCENE`, `MAX_ACTION`, `MAX_CAPTION_ZH` and `MAX_CAPTION_EN`.
  - It also shows 「儲存」 and 「取消」.
  - In edit mode, when `materials.backgroundUploaded`, the row shows 「背景是你上傳的圖，不會重畫」 under 場景.
- A row whose page has either flag shows the chip 「要重畫」.
- A failed save shows a toast with `errMsg(e, "沒辦法儲存這一頁")`, and the row stays in edit mode.

- [ ] **Step 1: Write the failing tests**

```tsx
// storyPreview.test.ts
it("flagged pages count in planCounts")
// StorySection.test.tsx
it("existing rows have 編輯; 儲存 sends only the changed fields")
it("取消 discards the inputs")
it("要重畫 chip when redraw_pose or redraw_background")
it("uploaded background row shows 背景是你上傳的圖，不會重畫 in edit mode")
```

e2e `setup-edit.spec.ts`:
1. Seed a book with 3 pages and produce until every page is 可以印.
2. In setup, 編輯 page 2's 動作 and 儲存. The row shows 要重畫.
3. In B, the card heading is 「還有 1 頁要重畫」. Press 繼續做.
4. Only page 2 gets a new pose generation; compare the generation lists per page before and after. Page 2 ends 請看一下.

- [ ] **Step 2: Run the tests and confirm they fail.** Implement them.
- [ ] **Step 3: Full verification.** Run the whole backend suite, the frontend checks, all of e2e, and `npm run gen:api` (expect no diff).
- [ ] **Step 4: Commit** with `feat(web): edit existing pages in setup; 要重畫 rows`.

---

### Task 16: GPU-gated: #8 prompt calibration and real-UI walkthrough

> **BLOCKED until the user explicitly says the GPU is free.** Do not start this task, not even its setup, before that message. This is not a subagent task; the controller does it after the final whole-branch review. Merge waits for it (spec §4.8).

**Files:**
- Create: `experiments/results/phase14_outline_prompt_<ts>/run.py`, `NOTES.md`, `results.csv`, `sheets/`
- Modify: `backend/app/services/prompts.py` and `tests/test_prompts.py`, only if the user picks a wording other than the candidate.
- Create: `experiments/results/fixes_acceptance_<ts>/NOTES.md` and screenshots.

- [ ] **Step 1: Announce before using the GPU.** Run `curl -s :8188/system_stats` and `nvidia-smi`. Tell the user the GPU is about to be used, and for what.
- [ ] **Step 2: Calibration script** (throwaway, following `experiments/results/phase9_all_qwen_20261110_1700/run.py`: `WorkflowRegistry` plus httpx to `http://127.0.0.1:8188`; no product DB):
  - The matrix: 2 character descriptions × 3 wordings: `thick clean black outlines` (today's baseline), `clean thin black outlines` and `clean medium-weight black outlines`.
  - Each wording replaces `OUTLINE_PHRASE` in all three templates.
  - For each character: 2 poses (`qwen_edit_pose_cutout` from that character) and 1 background (`qwen_background`). Fixed seeds, identical across wordings.
  - Metric: outline half-thickness, measured as in spec §4.5. Take the skeleton of `luma < 70` and report the distance-transform p75/p90 over skeleton pixels. Also render each image with `render_lineart(…, outline_fill=True)` and record QA `gap_count`.
  - Output: `results.csv`; contact sheets in `sheets/` (one row per wording, columns character | pose 1 | pose 2 | background, each as color + line art); and `NOTES.md` with timings and failures.
- [ ] **Step 3: Free the GPU.** `curl -X POST :8188/free -H 'Content-Type: application/json' -d '{"unload_models": true, "free_memory": true}'`.
- [ ] **Step 4: Send the contact sheet to the user** and ask them to choose the wording (AskUserQuestion, multiple choice). If they pick something other than `clean thin black outlines`, change `OUTLINE_PHRASE` and the test, rerun `tests/test_prompts.py`, and commit with `feat(prompts): calibrated OUTLINE_PHRASE`.
- [ ] **Step 5: Acceptance servers** (separate DB; never `storage-r2/app.db`):
  - backend on **8001**, using a settings copy whose `paths.database` and `paths.storage_dir` point to `experiments/results/fixes_acceptance_<ts>/storage`, with `storage.backend: local` and `comfy.url` on 8188;
  - frontend `npx next dev -p 3001` with `BACKEND_URL=http://127.0.0.1:8001`.
- [ ] **Step 6: Walk the real UI with Playwright, taking a screenshot at each step.** Announce GPU use again if time has passed.
  1. New book, character (×-delete one candidate, then undo), paste a 3-page story, 開始做這本書. B shows 「做好 N / 3 頁」 and never more than 3.
  2. B 選取多頁: 2 pages × 2 per page, 重畫. Both pages end 請看一下 and stay counted as printable.
  3. C on one of them: the history row shows 使用中 and 新. Click the other pose, then ↶ 復原. Set 一次畫 1 張 and press 重畫主角.
  4. Setup: 編輯 page 3's 動作. The row shows 要重畫. Back in B: 還有 1 頁要重畫, then 繼續做. Only page 3 changes.
  5. Compare the line art of a redrawn page with the color art: one outline, no chain beads.
  6. 複製給 AI 的指令 over the LAN hostname (`http://<hostname>:3001`). It copies, or the dialog shows the prompt selected.
  7. 印出這本書 downloads a PDF. Then delete the book from the bookshelf 「⋯」, and confirm its share link returns 404.
- [ ] **Step 7: Free the GPU again**, stop both acceptance servers, write `NOTES.md` (what passed, what failed, timings, screenshot list), send the screenshots to the user, and commit the notes, sheets and script with `exp: phase14 outline prompt calibration + fix-round acceptance`.

---

## Self-Review

**Spec coverage:**

| Spec item | Tasks |
|---|---|
| §4.1 delete a book | 4 (API, cancel and refund, storage prefix, worker orphan cleanup, GC sequence, `mine`) and 9 (menu, dialog, toast, e2e) |
| §4.2 delete candidates | 5 and 10 |
| §4.3 progress as pages | 6 (`progress_pages`) and 12 (copy, bar, ETA) |
| §4.4 history row and inline redraw | 7 (count, candidate flag) and 14 (row, 使用中/新, select, e2e) |
| §4.4 batch redraw | 7 (endpoint, take plus candidates, cap, review tag) and 13 (select mode, bar) |
| `review_pose` soft tag | 6 (ready, export, produce, 請看一下 filter, cleared by a pose pick or accept) |
| §4.5 single outline | 1 (column) and 2 (`stroke_fill` r=10 keep=13 at 832 px, scaled; `lineart_key`; new composites; no `RENDER_VERSION` bump) |
| §4.6 copy fallback | 11 |
| §4.7 setup edits | 1 (flags), 8 (flags set only by setup, uploads skipped, produce redraws as a take with review) and 15 (UI, e2e) |
| §4.8 prompts | 3 (constant and candidate wording) and 16 (GPU calibration, the user's pick) |
| book AUTOINCREMENT | 1, with the GC follow-up in 4 |
| No-GPU rule | Global Constraints; only Task 16 uses the GPU |

**Spec §5 tests:** every pytest bullet in §5 maps to a named test in Tasks 1–8. The vitest bullets map to Tasks 9–15. The four e2e flows are `delete-book.spec.ts` (9), `redraw-poses.spec.ts` (14), `setup-edit.spec.ts` (15) and `copy-prompt.spec.ts` (11).

**Type and name consistency:**
- `ATTENTION_REVIEW_POSE` and `REVIEW_POSE_DETAIL` are defined in Task 6 and used in Tasks 7 and 8.
- `CANDIDATE`, `REVIEW` and `redraw_jobs` are defined in Task 7 and used in Task 8.
- `fill_outlines` and `OUTLINE_FILL_VERSION` are defined in Task 2.
- `FIX_ROUND_ROUTES` is defined in Task 4 and extended in Tasks 5 and 7.
- `progress_pages`, `review` and `redraw_count` are defined in Tasks 6 and 8 and read in Task 12.
- `useRegenerate({count})` and `useSelectPoseC` are used only in Task 14.

**Review Focus:** each of the five items has its test in its owning task (4, 7, 2, 7, 14).
