# Fix round 2026-11-13: book deletion, candidate cleanup, page progress, pose redraw UX, single outline, copy prompt, story editing — design

Date 2026-11-13. Base: master c4248b8 (structure-cleanup af163ef merged). The user asked for these in conversation; the UI direction was approved from the mockups in the session scratchpad (`p14mock/A_overview_select.png`, `B_redraw_panel.png`, `C_delete.png`). These are copied into `docs/superpowers/specs/mockups/2026-11-13/` as part of this design.

The user is using the GPU during this phase. **Nothing in this phase may use the GPU or ComfyUI.** All tests mock the engine. #5 is decided with an offline CPU experiment on images that already exist.

## 1. Why

Using the app on a real 20-page book surfaced several problems:

- **No delete:** there is no way to delete a book, and none to delete unwanted character candidates.
- **Misleading progress:** the progress line grows forever (「做好 68 / xx 張」).
- **Redrawing a deformed pose is clumsy.** The user must:
  - open each page;
  - accept that 換一張 often still deforms;
  - cope with not being able to compare or go back.
- **Double outline:** the line art doubles the character's thick black outline (「多一個框」, with chain-like beads).
- **Copy fails over the LAN:** 「複製給 AI 的指令」 fails when the site is opened through the LAN hostname. The error also tells the user to select the text manually, but the text is never shown.
- **Pages are locked after creation:** existing pages can't be edited in 「主角與故事」.

## 2. User decisions (2026-11-13)

| # | Topic | Decision |
|---|---|---|
| 1 | Delete a book | Bookshelf card 「⋯」 menu → 「刪除這本書」 → ordinary confirm dialog (not type-the-title, not a 30-day trash). Copy: 「確定要刪除『{title}』嗎？」 / 「{n} 頁、所有圖和修過的線都會刪掉，刪了就救不回來。分享出去的連結也會失效。」 Buttons 「取消」 / 「刪除」. |
| 2 | Delete a main character | Only unwanted **candidate** images: each non-chosen candidate in 主角與故事 gets a 「×」. The chosen character has none, and uploads are not part of this. |
| 3 | Progress | Show pages, not jobs: 「做好 {ready} / {total} 頁」, computed from page states, so it can never exceed the page count. |
| 4A | Batch redraw | B overview gets a 「選取多頁」 mode. Card checkboxes, plus a bottom bar: 「已選 N 頁」, 「每頁畫 1/2/3 張讓我挑」 and 「重畫 N 頁的主角」 / 「取消」. After the redraw, each page **automatically uses the first new pose** and is marked 「請看一下」. It stays printable; the user can switch and then press 「這頁可以印了」. |
| 4B | Single-page redraw | C's 角色動作 row lists **every pose ever drawn for that page** as thumbnails: current one tagged 「使用中」, new ones tagged 「新」. Clicking one switches to it (undoable via ↶ 復原). Redraw controls are inline: action text, 表情 chips, 面向 chips, 「一次畫 1/2/3 張」 (**default 1**), and a 「重畫主角」 button. |
| 5 | Single outline | Fix the line-art extraction so a thick dark outline becomes ONE line. The user asked for it to be **automatic** (「自動判定」), not a manual 細／一般／粗 choice. It applies **only to new or redrawn pages**; existing pages keep today's line art (§4.5). |
| 6 | Copy the AI prompt | If `navigator.clipboard` fails, fall back to `document.execCommand("copy")`. If that fails too, open a dialog showing the whole prompt pre-selected, with 「已全選，按 Ctrl＋C（手機長按）複製」. |
| 8 | Thinner outlines at the source | The user traced the double outline to the generated character art having very thick outlines (most visible in 小熊尋找彩虹). Also change the generation prompts so new characters, poses and backgrounds get thinner outlines. The exact wording is calibrated on the GPU later (§4.8). |
| 7 | Edit pages in setup | Each existing page in 主角與故事's story list gets 「編輯」 for scene / action / 中文 / 英文. A page whose scene or action changed is marked 「要重畫」 and is redrawn only when the user presses 「繼續做」. Nothing is generated automatically. Caption-only edits apply immediately and need no redraw. |

## 3. Goals, non-goals, anti-goals

**Goals:** the seven decisions above, with tests (backend pytest, vitest, e2e where a flow changes) and no GPU use.

**Non-goals:**
- trash or undo for book deletion;
- deleting the chosen character or uploads;
- multi-page redraw of backgrounds;
- changing models (prompt wording changes only for #8);
- cloud work.

**Anti-goals:**
- Deleting a book must never touch another owner's data or leave R2 objects behind for that book.
- A batch redraw must never silently replace a pose the user can't get back. Every previous pose stays in the page's history row and undo works.
- The progress line must never show more done than total.
- Line-art changes must not re-render or demote existing pages: the user chose new/redrawn pages only.
- No GPU or ComfyUI calls in tests or experiments this phase. The #8 prompt calibration is the one exception and runs only after the user frees the GPU.

## 4. Design

### 4.1 Delete a book (#1)

**Backend:** `DELETE /api/books/{book_id}` (owner via `owned_book`; admin may delete only their own books, like everything else). In one transaction it:

- cancels the book's QUEUED generations and queued PageTasks, with a refund, reusing `cancel_owner_queue` semantics scoped to one book;
- deletes rows (pages, generations, page tasks, page history, lineart edits, usage rows tied to the book if any) and the book itself;
- returns 204.

Then, after commit, it deletes storage under `books/{book_id}/` with `storage.delete_prefix`. A failure there is logged; `storage_gc` cleans orphans later.

A RUNNING job for that book is not killed. Its result is discarded when it finishes: the worker already tolerates a missing page or book, which tests must confirm. Share links 404 because the book is gone.

**Frontend:**
- The bookshelf card (home page, own books only) gets a 「⋯」 menu with 「刪除這本書」 and the confirm dialog from the mockup.
- The existing 重新命名 and 主角與故事 entries are kept if the card already offers them; otherwise the menu holds only 刪除.
- After deletion the card disappears and a toast says 「已刪除「{title}」」.

### 4.2 Delete character candidates (#2)

**Backend:** `DELETE /api/books/{book_id}/character/candidates/{gen_id}`:

- 409 if `gen_id` is the book's `character_generation_id` or a running/queued job (cancel those via the existing cancel endpoint instead);
- 404 if it is not a CHARACTER generation of that book.

It marks the generation discarded (`discarded=True, discarded_at=now`, the same mechanism used for other discarded images, so storage_gc removes the object after its retention), so it disappears from the candidate list.

**Frontend:** the 「×」 on each non-chosen candidate card in CharacterSection calls this endpoint.

### 4.3 Progress as pages (#3)

**Backend overview:** add `progress_pages = {ready, total}`:

- `total` = number of pages;
- `ready` = pages that are PASSED, not busy, and whose attention is empty **or `review_pose`** (see §4.4: a batch-redrawn page stays printable and counts as done).

The old job counters (`progress.queued/running/done`) stay for the ETA and the 「製作中」 heuristics but are no longer shown as 「做好 N / M 張」.

**Frontend:**
- The B header line becomes 「做好 {ready} / {total} 頁」, with an ETA line 「・剩約 N 分鐘」 only while jobs are active.
- The progress bar shows ready/total.
- Mockup A shows 「做好 20 / 20 頁」.

### 4.4 Pose redraw UX (#4)

**History row (4B):**
- The page's pose history is every successful, non-discarded POSE generation with this `page_id`, newest first, including uploads if poses can be uploaded (they can't today).
- Data: the existing `GET /api/books/{id}/generations?kind=pose&page_id=` (builder check). Selecting uses the existing `POST /pages/{id}/pose?oneclick=true`; the toast 「復原」 for candidate removal uses `PATCH /generations/{id} {discarded}`.
- Clicking a thumbnail selects that pose through C's existing select-pose path with oneclick (snapshot first, then recompose), so ↶ 復原 restores the previous one.
- A thumbnail is 「新」 if it was created after the page's last visit to C. Use local storage per page, or created within the current session; builder's call, keep it simple.

**Inline redraw:**
- The action text, 表情 and 面向 chips already exist in C's PoseRow and are reused.
- Add 「一次畫 [1][2][3] 張」 (default 1) and 「重畫主角」. That queues N take-generations (first auto-taken, as today's 換一張) or N plain candidates (shown in the row, not taken).
- Decision for the builder: **the first one is taken** (as today), the rest are candidates.

**Batch (4A):**
- B gets 「選取多頁」; cards show checkboxes; the bottom bar has candidates 1/2/3 (default 1) and 「重畫 N 頁的主角」.
- Backend: `POST /api/books/{id}/poses/redraw {page_ids, count}` queues, per page, `count` pose generations using the page's current action, expression and facing. The first is a take, so it auto-swaps with an undo snapshot when done (existing behaviour); the others are candidates.
- Batch-redrawn pages get `attention="review_pose"` (new value, label 「請看一下：換了新的主角動作」), so they show 請看一下. The tag is set when the page's pipeline **finishes** (recompose → line art → QA), because the pipeline clears attention on completion. 「這頁可以印了」 clears it as today. Clicking another pose in the history row also clears it.
- `review_pose` is a **soft** tag, unlike `failed` and the other attention values:
  - the page still counts as ready in `progress_pages`, the bookshelf 「N / M 頁可以印」 and the 可以印 filter;
  - it is included in B's default 「印出這本書」 (`include=ready`);
  - it also appears under the 請看一下 filter chip;
  - `/produce` (繼續做) does **not** re-queue a page just because it is `review_pose`.
- Candidates (the 2nd and 3rd poses of a redraw) need a flag in their request so the auto-select path never takes them. Only the take is auto-selected.
- Usage: each generation counts against the daily cap. The plan endpoint or the bar shows 「會畫 N 張」.

### 4.5 Single outline (#5)

**Experiment (CPU only, done 2026-11-13):** `experiments/single_outline.py`. Results and contact sheets are in `experiments/results/phase14_single_outline_20261113_1009/`. Inputs:
- the 20 demo-book composites (`input/`);
- the 20 composites of the user's book 小熊尋找彩虹 (`input_bear/`, read-only copy from R2; not committed).

| Method | Result |
|---|---|
| a, current xdog | xdog traces both edges of a thick outline, then skeletonize gives two lines plus chain beads. |
| b, fill every dark pixel (luma < T) | Outlines become one line. But eyes become 「＋」 and dark regions such as a night sky turn into a polygon mesh. Rejected. |
| c, archived `hybrid` | Tube-like double outlines everywhere. Rejected. |
| d, fill dark minus its opening | Eyes are kept, but stroke junctions survive as blobs and become beads. Rejected. |
| **e, stroke fill (chosen)** | Fills only **stroke-like** dark pixels: dark minus the opening, plus thick blobs that are not "solid" (junctions). Solid dark shapes keep xdog's outline: eyes, nose, a night sky. |

**Chosen method, `stroke_fill`:**
- `dark = luma < 70`;
- `thick = open(dark, ellipse radius r)`;
- `dt = distanceTransform(dark)`;
- for each 8-connected component of `thick`, find its max `dt`. A component is **solid** when that max is ≥ `keep`;
- set the xdog gray to 0 at pixels in `dark & ~solid`, then run `render_ink` unchanged.

**Parameters:** `r = 10`, `keep = 13`, measured at the composite's 832 px width. They scale with the source width: `r = round(10 * w / 832)`, `keep = 13 * w / 832`.

**Automatic per the user:** one fixed setting handles both books.
- Demo fox pages: the r10 result is the same as r8.
- Bear pages, where the outlines are thicker: r10 removes the double lines that r8 leaves on 小熊尋找彩虹 p7.
- No manual 細／一般／粗 control.

**Known small artifact:** the rim of a large dark area (night-sky edge) can leave a few short dashes. Accepted.

**Applies only to new or redrawn pages (user decision):**
- New Page column `outline_fill: bool`, migration 0015:
  - existing rows default False;
  - new pages are created True.
- When a page's composite changes, set it to True. That covers a new pose or background, 換一張, batch or inline redraw, an upload, and a recompose from 繼續做.
- `outline_fill` is part of `lineart_key`'s digest. The pinned-inputs test is updated deliberately.
- `RENDER_VERSION` is **not** bumped, so existing pages keep their cached line art and status.
- `render_lineart(color, content_px, detail, width, *, outline_fill: bool)` applies `stroke_fill` before `render_ink` when True.
- The QA, autofix and LineartEdit layers are unchanged. Pen and eraser strokes on an old page are never re-aligned, because old pages never switch automatically.
- A legacy page whose selected art is a whole `page` generation also follows the flag. It is False unless that page is redrawn.
- **No UI:** no new button. A redrawn page simply gets the new line art.

### 4.6 Copy prompt fallback (#6)

- A helper `copyText(text): Promise<boolean>` in `lib/` tries `navigator.clipboard.writeText` when `window.isSecureContext`, else falls back to a hidden textarea plus `document.execCommand("copy")`.
- On false, it opens a Dialog 「複製給 AI 的指令」 with a read-only textarea holding the prompt, auto-selected on open, plus the hint text.
- InviteAdmin's copy uses the same helper. Its "link shown next to it" fallback stays.

### 4.7 Edit existing pages in setup (#7)

- Each existing page row in StorySection gets 「編輯」. That opens inline inputs for 場景, 動作, 中文 and 英文 (same limits as the parser), with 「儲存」 and 「取消」.
- Save PATCHes the page with the existing endpoint plus a `from_setup: true` marker (or a dedicated setup endpoint; builder's call).
- **Stored flags, not a derived comparison** (builder check):
  - New Page columns `redraw_background: bool` and `redraw_pose: bool`, migration 0015 alongside `outline_fill`.
  - They are set **only** by setup edits that change the scene (→ background) or the action (→ pose) on a page that already has that art.
  - C's 改動作 and 表情/面向 chips keep their current behaviour, which saves without a redraw flag.
  - Uploaded backgrounds: a scene edit on a page whose background is an upload **only saves the text** and does not set `redraw_background`. The row shows 「背景是你上傳的圖，不會重畫」.
- While a flag is set, the row shows 「要重畫」.
- 「繼續做」 (`/produce`) queues a new background and/or pose for flagged pages, within the cap, like empty slots today. It clears each flag when it queues that page.
- The new pose replaces the current one as a take, with an undo snapshot, and the page is tagged `review_pose` (same as a batch redraw).
- Caption edits need no redraw.

### 4.8 Thinner outlines in generation prompts (#8)

- `backend/app/services/prompts.py` has `thick clean black outlines` in three templates: QWEN_CHARACTER, QWEN_POSE and QWEN_BACKGROUND.
- Move the phrase into one constant, `OUTLINE_PHRASE`.
- Change it to the calibrated wording. The candidate is `clean thin black outlines`; the final wording is picked from the GPU calibration.
- **GPU calibration (deferred until the user frees the GPU; announce first, `POST /free` after):**
  - 2 character descriptions × 2 wordings (`thin` vs `medium-weight`) plus today's `thick` as the baseline;
  - for each, 2 poses and 1 background;
  - measure the outline half-thickness the same way as §4.5 (skeleton `dt` p75/p90 inside dark strokes) and run the line art with `outline_fill`;
  - send the user a contact sheet and let them pick;
  - record the results in `experiments/results/phase14_outline_prompt_<ts>/`.
- Only new generations are affected. Existing characters and poses keep their look; a pose edited from an existing thick-outline character may stay thick, and §4.5 handles that.
- Until the calibration runs, the branch carries the constant with the candidate wording, and the plan's last task is the calibration. **Merge waits for it.**

## 5. Success criteria

- **pytest:**
  - delete book (rows + storage prefix + other owners untouched + running job tolerated + share 404);
  - delete candidate (409 for the chosen one, discarded);
  - progress_pages;
  - batch redraw endpoint (cap, take + candidates, attention review_pose);
  - needs-redraw detection + produce picks those pages;
  - `stroke_fill` unit tests (thick band → one skeleton line; solid disk ≥ keep keeps its outline; junction blob filled; scale with width);
  - `outline_fill` flag: new pages True, existing rows False after migration, composite change sets True, part of lineart_key, RENDER_VERSION unchanged;
  - review_pose soft tag: counts ready, included in default export, not re-queued by produce;
  - setup redraw flags set/cleared, upload pages not flagged.
- **vitest:** every UI change above (menu + dialog, ×, progress text, select mode + bar, history row + inline redraw, copy fallback + dialog, edit rows + 要重畫).
- **e2e (fake backend):**
  - delete a book from the bookshelf;
  - select 3 pages, redraw, the pages show 請看一下 and history rows grow;
  - edit a page in setup, then 繼續做 redraws only that page;
  - copy prompt on an insecure origin shows the dialog.
- **Offline #5 experiment:** contact sheets plus NOTES.md (done; method e chosen, automatic).
- **#8:** the prompt constant, with tests updated. The GPU calibration and the user's pick happen before merge.
- **No GPU use** until the user frees the GPU. After that come the #8 calibration and a real-GPU walkthrough.

## 6. Calls made by the author

- Batch redraw marks pages with a new attention value `review_pose`, reusing the existing 請看一下 UI. It is not a new state.
- Book deletion is a hard delete with best-effort storage cleanup after commit (orphans picked up by storage_gc).
- Candidate deletion reuses `discarded` rather than deleting rows immediately.
- Inline redraw takes the first new pose automatically, as 換一張 does today; extra ones are candidates.
- 「新」 badge: session/local-storage based, no DB field.
- Needs-redraw uses stored flags set only by setup edits. This replaces the earlier derived-comparison idea, which the builder check showed would flag C's chip edits and upload pages.
- Uploaded background + scene edit in setup: save the text only, no redraw.
- A redraw caused by a setup edit replaces the pose as a take and tags the page `review_pose`.
- `review_pose` is a soft tag: it counts as ready and is printed by default.
- #5: one automatic setting (r = 10, keep = 13 at 832 px), stored per page as `outline_fill`. There is no manual control, and RENDER_VERSION is not bumped.
- #8: GPU calibration before merge.
- Book ids: add AUTOINCREMENT to `book` (migration 0015). A deleted id is never reused, so `storage_gc`'s "id > max → keep" rule stays safe. The worker deletes what it just wrote if the book vanished mid-job.
- Mockup A's 「請挑一張」 note and its default of 2 are superseded by the decision table (first pose auto-used, default 1).
- Bookshelf menu holds only 刪除 (the card has no rename / 主角與故事 entries today).
