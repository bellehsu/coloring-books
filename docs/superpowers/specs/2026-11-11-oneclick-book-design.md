# Phase 13: make a whole book in one click (2026-11-11)

Approved mockups: `docs/superpowers/specs/mockups/1_create.png`, `2_book.png`, `3_fix.png` and `4_phone.png` (editable canvas: https://claude.ai/artifact/RpYAFqVru2jidZinzGLhpx). Where the mockups mention a 「動作包」, this text overrides them: the pack was dropped (§2).

## 1. Why
The user found the current flow choppy:
- There are too many steps. The user jumps between the 角色／故事／頁面 tabs, and every page needs 構圖 → 補線 → 對 → 完成.
- The waiting is scattered.
- There is no view of the whole book.
- Fixing lines and finishing pages is tedious.

The user's chosen ideal: 「一鍵做完，只看需要我處理的頁」.

## 2. User decisions (2026-11-11)
- **Flow:**
  - **A 做一本新的著色書**: one page with three sections (書名和紙張, 主角, 故事) and one button, 「開始做這本書」.
  - Everything after that runs automatically.
  - **B 整本書** shows progress and every page with its state.
  - **C** handles one page.
  - **D** is the phone layout.
  - The old 角色／故事／頁面 tabs and the per-page editor are replaced. The old editor stays reachable as 「進階編輯」.
- **No pose pack.** Each page draws its own pose from its story action: one pose per page, with any vehicle or object in the action drawn as written. This uses the Phase 12 regenerate variations and the 表情 and 面向 chips.
  - The user worried about consistency without a pack. Consistency comes from editing the same character reference, so it is the same either way; Phase 11's acceptance had 10 of 10 pages consistent with no pack. The user chose 「每頁照故事畫，不自動產生動作包」.
- **Keep the cut-out composite** (background plus cut-out pose, pasted). The user declined a "more natural composition" experiment.
- **Auto 「可以印」:**
  - A page is print-ready as soon as its pipeline finishes. There is no per-page 對 or 完成.
  - Before printing, the user glances at all the thumbnails.
- **Lines:** unclosed lines are only a soft hint (「N 處沒接起來（可以不管）」), never a reason for attention. Auto line-fix still runs.
- **「請看一下」** is reserved for pages where a generation or a pipeline step failed.
- **Undo:**
  - C has 「↶ 復原」 and 「↷ 重做」.
  - Each item (background, pose, lines) has 「還原成自動畫的那張」.
  - When re-composing (dragging the character, or a new background or pose), the user's own pen strokes are **cleared without a dialog**, and 「↶ 復原」 brings them back.
- **Daily cap too small for the whole book:** run what fits now, and finish the rest tomorrow. B shows 「還有 N 頁明天繼續」 and a 「繼續做」 button.
- **Existing books:**
  - B shows them as they are: PASSED pages are 「可以印」, the others are 「還沒做完」.
  - 「繼續做」 fills only what is missing. Nothing changes old pages automatically.
- **Uploads:**
  - Background upload per page stays: `AI 畫背景 / ↑ 上傳背景` in A's story preview and in C.
  - **New:** 「↑ 上傳自己的主角」 in A. The user chose 「要，直接做」, with no spike.
- **No cover.**
- **Visual style:** 「童趣繪本」 for the whole product:
  - cream dotted ground `#FFF8EE` with `#F1E2CC` dots;
  - 2 px ink borders `#2B2420`;
  - hard ink drop shadows `0 5px 0 #2B2420`;
  - pill buttons;
  - primary orange `#F2A33A` with ink text; sky `#CFE6FF`; green `#D8F0D2` / `#7FC97A`; attention `#FFD7A6` / `#E07B1F`; muted `#6B5F55`;
  - Noto Sans TC, with 900-weight headings.
- Earlier features still hold: the daily cap and usage counting, sharing, invites, captions, the theme pack, and the Phase 12 chips and variations.

## 3. Goals, non-goals, anti-goals
- **Goals:**
  - one click from a filled A page to a finished book;
  - whole-book status at a glance;
  - only failures need attention;
  - undo everywhere on C;
  - character upload;
  - the new visual style across the app.
- **Non-goals:**
  - a cover;
  - a pose pack;
  - web push notifications. 「好了會通知你」 is an in-app banner and toast plus the tab title (「✓ 做好了」);
  - multiple characters;
  - changing the generation models or the composite method.
- **Anti-goals:**
  - a page silently losing user edits without a way back. Undo covers this;
  - an automatic step overwriting something the user chose: background, pose, upload, placement or caption;
  - the run exceeding the daily cap.

## 4. Design

### 4.1 「開始做這本書」 (one-click run)
- `GET /api/books/{id}/produce/plan` returns:
  - the counts by kind: backgrounds for pages in AI mode with none selected, and poses for pages with an action and none selected;
  - `estimate_s` (about 22 s per image);
  - `remaining` today;
  - `fits`: how many jobs fit in today's remaining cap.
- `POST /api/books/{id}/produce` does the following:
  - It enqueues missing jobs in page order. For each page, the background comes before the pose.
  - It reserves only as many jobs as fit today, atomically, and returns `{queued, deferred_pages}`.
  - Pressing it again never duplicates work. A page with a QUEUED or RUNNING job of a kind is skipped for that kind.
  - With 0 remaining it returns 429 「今天的生成次數已用完，明天再來」. Partial runs are not errors.
- **Page pipeline** (server side, single worker):
  - **When it runs.** A page gets a pipeline task when one of these happens:
    - one of its background or pose generations succeeds;
    - the user changes its background, pose or placement on C;
    - 「繼續做」 finds a page that has both images but no composite.
  - **Steps:**
    1. Auto-select the newest successful background and pose, unless the user selected one; the user's choice wins.
    2. Compose with the saved placement, or the default (`services/compose.py` already has a server-side default).
    3. Render the line art.
    4. Auto-fix the gaps into the **auto stroke layer**.
    5. Mark the page PASSED (`passed_at` set) and clear `attention`.
  - **On failure**, the page gets `attention = "failed"` and `attention_detail` holds the zh message. A failed generation also sets `attention = "failed"`.
  - **Storage.** Tasks live in a new table, `page_task(id, page_id, kind, status, created_at, error)`. The existing worker claims them after the queued Generations (GPU first, CPU tasks in between). On restart, RUNNING tasks are re-queued, unlike generations, because the tasks are deterministic and cheap.
  - **Records.** The pipeline records what it chose in `page.auto_background_generation_id` and `page.auto_pose_generation_id`, for 「還原成自動畫的那張」.
- **PASSED semantics in the new flow:**
  - The pipeline sets PASSED without `character_check`.
  - The old paths that reset PASSED to DRAFT (line edits, prompt edits, re-compose) are routed through the pipeline instead. A change re-runs it, and it ends PASSED again unless it fails.
  - The old editor (進階編輯) keeps its own behaviour.

### 4.2 Line edits: auto layer and user layer
- The stroke layer splits into `auto` (written by auto-fix) and `user` (pen and eraser).
- Print uses both.
- 「還原成自動畫的那張」 for lines clears the `user` strokes and keeps `auto`.
- Re-composing clears both. The pipeline regenerates `auto`, and the `user` strokes live on in the undo history.

### 4.3 B 整本書 (book overview)
- **Routes.** `/books/{id}` becomes the overview. The old editor stays at `/books/{id}/editor` (「進階編輯」). The old `/character` and `/story` routes redirect to A (`/books/{id}/setup`).
- **Status card:**
  - While running: overall progress over queued jobs, the remaining-time estimate, and 「可以先去忙，好了會通知你」.
  - When done: 「整本做好了！」, a per-page segmented bar, the counts, and 「去看那一頁 →」 if any page needs attention.
  - With deferred pages: 「還有 N 頁明天繼續」 and 「繼續做」, enabled when the cap allows.
- **Filters:** 全部, 請看一下, 可以印 and 還沒做完.
- **Page cards:**
  - the composed line-art thumbnail with its caption;
  - a status pill: 可以印 ✓, 請看一下, 製作中 or 還沒做完;
  - a soft note: 「N 處線沒接起來（可以不管）」, or the failure message.
  - Any card opens C for that page.
- **「印出這本書」** opens an export sheet. It shows every page thumbnail first (the 「看一眼」 step), then the existing PDF options.
- **Updates.** B polls the existing endpoints. When the run finishes it shows a toast and the tab title changes.

### 4.4 C 處理一頁 (one page, with undo)
- **Header:**
  - `← 整本書`;
  - the page title and status;
  - 「↶ 復原」 / 「↷ 重做」;
  - previous and next page (prev/next among attention pages when the page was opened from that filter).
- **Preview:**
  - toggle between 要印的線稿 (print.png at preview size, with captions) and 彩色原圖;
  - a checkbox, 「圈出沒接起來的線（N 處）」, which overlays the QA gap markers;
  - dragging the character saves the placement, then the pipeline re-runs.
- **Item rows:**
  - **角色動作:** 換一張 (Phase 12 variation), plus an action-text edit with the 表情 and 面向 chips;
  - **背景:** AI 再畫一張, ↑ 上傳圖片;
  - **線條:** 自動補線, 畫筆 and 擦除 (reuse LineFixCanvas);
  - **圖下方的字:** 改字.
  - Each item shows 「↺ 還原成自動畫的那張」 when it differs from the `auto_*` choice.
- **Reason card.** Shown only for `attention = "failed"`, together with 「再試一次」.
- **「這頁可以印了 ✓」** is available whenever the page has a composite.
  - It clears `attention` and keeps PASSED.
  - It moves on to the next page in the current filter.
- **Undo history:**
  - Table: `page_history(id, page_id, seq, snapshot_json, created_at)`.
  - A snapshot holds:
    - `background_generation_id`, `pose_generation_id`, `placement`;
    - `action_prompt`, `caption_zh`, `caption_en`;
    - the `user` strokes themselves.
  - Every user change made on C pushes a snapshot first.
  - 復原 and 重做 move along the history and apply the snapshot. The pipeline re-runs when images or placement differ, and the `user` strokes are restored afterwards.
  - At most 50 snapshots per page; the oldest are dropped.

### 4.5 A 做一本新的著色書 (create and setup)
- **Routes.** `/books/new` creates the book as soon as section 1 is valid, because generation needs a book id. `/books/{id}/setup` is the same page for an existing book (「主角與故事」).
- **Sections:**
  1. **Book name and paper.**
  2. **主角:**
     - description → 產生, and 「再畫一隻」 (one image at a time, as in Phase 9);
     - choose one;
     - **↑ 上傳自己的主角**.
  3. **故事:**
     - paste, 套用主題包, 複製給 AI 的指令;
     - a preview list of the parsed pages, each with an `AI 畫背景 / ↑ 上傳背景` toggle and a file picker.
- **Bottom bar:**
  - It shows the counts from `/produce/plan`, the minutes, and the cap ("今天會用 N 次，還剩 M 次"). If the book does not fit today, it says 「今天先做 X 頁，其餘明天繼續」.
  - 「開始做這本書」 creates or updates the pages through the existing import rules, applies the uploads, calls `/produce`, and goes to B.
- **Character upload:** `POST /api/books/{id}/character/upload` (multipart).
  - It uses the same validation as the background upload.
  - It creates a `CHARACTER` generation with `request_json.uploaded = true`.
  - The image is normalized to the character size and letterboxed on white, with no background removal.
  - The result is selected as the character.
- **Changing the character.** Today, setting the character a second time is a 409 (`api/books.py`). The new flow allows the change after a confirmation.
  - Existing poses stay.
  - New poses use the new character.
  - 「重畫全部動作」 is a later extension and is not built now.

### 4.6 Visual style
- Replace the design tokens in `frontend/src/app/globals.css` with the 童趣繪本 palette of §2.
- Restyle the CSS modules to match: 2 px ink borders, pill buttons, hard shadows and a dotted ground.
- WCAG AA contrast, visible focus rings, and touch targets of at least 44 px.
- **D (phone):** a two-column page grid, a sticky 「印出這本書」, and a single column on A and C.

### 4.7 Housekeeping
- `scripts/storage_gc.py` protects the generations referenced by `auto_*`, by `page_history` snapshots and by RUNNING or QUEUED `page_task` rows.
- Ownership: every new route goes through the `owned_*` guards, and unknown or foreign ids return 404.
- The shared-view and export rules are unchanged: PASSED pages only.

## 5. Success criteria
- **Backend unit tests:**
  - `/produce/plan` counts.
  - `/produce`:
    - a partial run within the cap;
    - deferred pages;
    - no duplicates when pressed twice;
    - 429 at 0.
  - Pipeline:
    - triggers;
    - auto-select respects the user's choice;
    - compose → lineart → autofix → PASSED;
    - failure → `attention`;
    - restart re-queues.
  - Undo and redo, the history cap, and item restore.
  - Split stroke layers.
  - Character upload and character change.
  - Existing-book display states.
  - Ownership 404 on every new route.
  - The migration up and down.
  - GC protection.
- **Frontend:**
  - vitest for A, B, C and the export sheet.
  - e2e with the fake backend:
    1. Create a book with the theme pack.
    2. Upload one background.
    3. Press 開始做這本書; B reaches 整本做好了.
    4. Open a page and press 換一張; then 復原 brings back the old pose.
    5. Press 這頁可以印了, then export the PDF.
  - 390 px with no horizontal overflow on A, B and C.
- **Real-GPU acceptance (ask first):**
  - The vehicles theme pack with the bunny: from an empty book to a PDF in one click. Record the total time and any attention pages.
  - One uploaded character (a hand-drawn-style image) gives usable poses.

## 6. Calls made by the author (open to change)
- `page_task` table processed by the existing worker.
- Snapshots, not diffs, capped at 50.
- Split auto and user stroke layers.
- Routes: `/books/{id}` is B, `/books/{id}/setup` is A, `/books/{id}/editor` is 進階編輯.
- 「好了會通知你」 is in-app only.
- The uploaded character is letterboxed on white, with no background removal.
- Changing the character keeps existing poses.
