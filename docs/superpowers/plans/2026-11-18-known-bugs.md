# 四個已知 bug 修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改紙張／列印方式後，任何舊版面座標的筆畫寫入都不能再蓋回換算好的筆畫；「看過了 ✓」與整本進度顯示正確。

**Architecture:** 伺服器端：筆畫層寫入（`commit_layer`、`put_edits`、自動補線 API）在同一個條件式 UPDATE 裡確認書本版面（紙張、列印方式）
沒變，變了就當作過期（流程重試、API 回 409）。用戶端：改版面前等待待存寫入、改版面後重新載入筆畫，409 時重抓。另外兩個是小的前端顯示修正。

**Tech Stack:** FastAPI、SQLModel、SQLite、pytest；Next.js、React、TypeScript、TanStack Query、vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-11-18-known-bugs-root-causes.md`（根因與檔案行號都在這裡）

## Global Constraints

- 分支 `fixes-2026-11-18`（從 main），不開 worktree。
- 不改資料庫結構（沒有新 migration）：版面條件用 `book.paper_size`、`book.print_mode` 比對。
- 錯誤訊息用中文；新的 409 訊息：「紙張或列印方式剛改過，請重新整理這一頁再畫」（後端常數，前端顯示同一句）。
- `experiments/results/` 不進 git；驗收紀錄寫在那裡但不提交。
- 後端測試：`env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/pytest <path> -q`；前端：`cd frontend && npx vitest run <file>`；e2e 照 README。
- 不需要 GPU，Task 6 驗收除外（要用 GPU 先問使用者並與 roboskin-7a session 協調）。

## Review Focus

1. 版面改了又改回原樣（A4→A5→A4）期間的舊寫入：條件比對的是「寫入當下」與「計算當下」的版面，改回原樣後舊座標其實正確 → 允許寫入（Task 1、2 測試 `..._layout_changed_back_is_accepted`）。
2. 用戶端有寫入失敗（離線）時按儲存設定：不能送出 PATCH，要顯示錯誤（Task 3 測試）。
3. 409 過期後使用者繼續畫：重新載入後的新筆畫要能正常存（Task 3 測試「409 後下一筆用新座標」）。
4. 只有 review_pose 的 PASSED 頁仍算完成（Task 5 保留既有測試）。
5. 流程重試上限：版面在流程跑的時候連續改很多次，不能無限重試（沿用 `_Stale` 既有的重試上限；Task 1 測試確認最後一次版面有被採用）。

---

### Task 1：伺服器端版面檢查（流程＋自動補線 API）

**Files:**
- Modify: `backend/app/services/page_lineart.py`（`commit_layer`、`ink_before_user_layer`）
- Modify: `backend/app/services/pipeline.py`（`_attempt`，約 :390-447）
- Modify: `backend/app/api/lineart.py`（`autofix`，:230 起）
- Test: `tests/test_pipeline.py`、`tests/test_api_lineart.py`

**Interfaces:**
- Produces: `Layout key` 型別 `tuple[str, str]` ＝ `(book.paper_size, book.print_mode)`（字串值，與資料庫欄位相同）；
  `page_lineart.book_layout_key(session, page_id) -> tuple[str, str]`；
  `commit_layer(..., *, layer, demote=True, layout: tuple[str, str] | None = None) -> bool` — `layout` 不是 None 時，條件式 UPDATE 另外要求頁面所屬書本的紙張與列印方式仍等於它，否則 rollback 回 False；
  `ink_before_user_layer(..., *, with_auto=True, layout: tuple[str, str] | None = None)` — 給了就用這個版面算（不再自己讀書本）；
  常數 `page_lineart.STALE_LAYOUT = "紙張或列印方式剛改過，請重新整理這一頁再畫"`。

- [ ] **Step 1：失敗的測試**
  - `tests/test_pipeline.py::test_layout_change_mid_run_recomputes_auto_with_new_layout`：照 `test_retries_when_page_changes_mid_run` 的寫法，草稿頁；monkeypatch `pipeline.plan_autofix`，第一次呼叫算完後送出改列印方式的 PATCH；跑完佇列。斷言頁面 PASSED，且 auto 層 ＝ `monkeypatch.undo()` 後重新跑一次 `run_pipeline` 得到的 auto 層。
  - `tests/test_pipeline.py::test_layout_changed_back_is_accepted`：計算中 A4→A5→A4 → 寫入成功、不重試（計算版面＝寫入版面）。
  - `tests/test_api_lineart.py::test_autofix_with_layout_changed_mid_request_is_409`：monkeypatch 讓自動補線計算後改版面 → 409、detail ＝ `STALE_LAYOUT`，已存的層不變。
- [ ] **Step 2：** 跑上面三個 → FAIL。
- [ ] **Step 3：** 實作：`_attempt` 開頭讀一次 `book_layout_key`，傳給 `ink_before_user_layer` 與 `commit_layer`；`commit_layer` 回 False 時照現有路徑丟 `_Stale`（既有重試）。自動補線 API 同樣傳版面，False 且原因是版面時回 409 `STALE_LAYOUT`（選用圖變了的既有 409 不變）。
- [ ] **Step 4：** 三個測試 PASS；`tests/test_pipeline.py tests/test_api_lineart.py tests/test_page_lineart.py` 全部 PASS。
- [ ] **Step 5：** Commit `fix(api): line-fix layers are only saved for the layout they were computed on`。

### Task 2：`put_edits` 版面檢查

**Files:**
- Modify: `backend/app/api/schemas.py`（`EditsIn`）、`backend/app/api/lineart.py`（`put_edits`）
- Test: `tests/test_api_lineart.py`

**Interfaces:**
- Consumes: Task 1 的 `commit_layer(layout=...)`、`STALE_LAYOUT`。
- Produces: `EditsIn.content_px: tuple[int, int] | None = None`（用戶端畫這些筆畫時的 `book.layout.content_px`）。給了而且 ≠ 目前書本 `layout.content_px` → 409 `STALE_LAYOUT`，不寫入。沒給（舊用戶端）→ 照舊。給了且相同 → 把當下的 `book_layout_key` 傳給 `commit_layer`（防止驗證到寫入之間版面又改）。

- [ ] **Step 1：失敗的測試**（照 `test_change_size_recomputes_and_demotes` 的設定）
  - `test_put_edits_with_stale_layout_is_409`：改列印方式後，用舊 `content_px` PUT → 409、detail ＝ `STALE_LAYOUT`、已換算的 user 層不變。
  - `test_put_edits_with_current_layout_saves`：用新 `content_px` → 200。
  - `test_put_edits_without_content_px_still_saves`：不帶 → 200（相容）。
- [ ] **Step 2：** FAIL → **Step 3：** 實作 → **Step 4：** PASS（含 `tests/test_api_lineart.py` 全部）。
- [ ] **Step 5：** Commit `fix(api): reject line-fix edits drawn on an old layout`。

### Task 3：進階編輯在改版面前後的處理（前端）

**Files:**
- Modify: `frontend/src/features/book/BookSettingsDialog.tsx`（submit）、`frontend/src/api/hooks.ts`（`useUpdateBook.onSuccess`）、
  `frontend/src/features/editing/LineFixStage.tsx`（key :47、onChange :115-121）、`frontend/src/features/editing/editsQueue.ts`（送出 `content_px`、409 處理）
  （實際路徑以 grep 為準，spec 有行號）
- Test: `frontend/src/features/editor/EditorPage.test.tsx`、`frontend/src/features/editing/LineFixStage.test.tsx`、editsQueue 的既有測試檔

**Interfaces:**
- Consumes: Task 2 的 `content_px` 欄位與 409 `STALE_LAYOUT`。
- Produces: edits PUT body 一律帶 `content_px`；`LineFixStage` key ＝ `${pageId}-${genId}-${contentPx[0]}x${contentPx[1]}`。

- [ ] **Step 1：失敗的測試**
  - EditorPage：「改列印方式後：補線畫布換成伺服器換算後的筆畫，下一筆不會把舊座標送回去」— `search` 為 `page=1&mode=line`、`edits=[[0.5,0.05]]`；PATCH mock 更新 `content_px` 並把 edits 換成換算後的點；點 繪本設定 → 印刷廠 → 儲存 → `stub-draw`；斷言最後一次 PUT 的 `strokes[0].points` ＝ 換算後的點，且 body 的 `content_px` ＝ 新值。照 :504、:529 用 `gate` 的寫法。
  - EditorPage：PUT 被 gate 卡住時按儲存 → 在放行前沒有 PATCH；放行後才 PATCH。
  - EditorPage：有寫入失敗時按儲存 → 不送 PATCH、顯示 `BookWritesError` 的訊息。
  - editsQueue／LineFixStage：PUT 回 409（`STALE_LAYOUT`）→ invalidate `["edits"]` 與書本、清掉 `latest`、顯示訊息；之後的筆畫用新座標存成功。
- [ ] **Step 2：** FAIL。
- [ ] **Step 3：** 實作：設定對話框在紙張或列印方式有變時先 `await whenBookWritesIdle(qc, book.id)`（`features/editing/pendingWrites.ts:58`），丟 `BookWritesError` 就顯示錯誤、不送 PATCH；`useUpdateBook.onSuccess` 加 invalidate `["edits"]`；key 加 contentPx；PUT 帶 `content_px`；409 處理如上。
- [ ] **Step 4：** 新測試 PASS；`npx vitest run` 全部 PASS；`npx tsc --noEmit` 無錯。
- [ ] **Step 5：** Commit `fix(web): advanced editor reloads strokes after a layout change and never saves old coordinates`。

### Task 4：「看過了 ✓」在重新合成時隱藏，409 時重抓

**Files:**
- Modify: `frontend/src/features/editor/FinishPanel.tsx`（:65-66 `review`，`doAccept` :152-164）
- Test: `frontend/src/features/editor/FinishPanel.test.tsx`

- [ ] **Step 1：失敗的測試**（用 `responses` map，照 :210、:230）
  - `recompose_pending: true` 的 PASSED＋review_pose 頁 → 沒有「看過了 ✓」按鈕。
  - `/accept` 回 409 → 顯示 toast，且 pages query 被 invalidate（同 `answer()` 在 :136 的做法）。「完成這一頁 ✓」同一路徑也重抓。
- [ ] **Step 2：** FAIL → **Step 3：** `review` 加 `&& !page.recompose_pending`；`doAccept` 的 catch 遇 409 invalidate `qk.pages(book.id)` → **Step 4：** PASS（含檔案全部）。
- [ ] **Step 5：** Commit `fix(web): hide 看過了 while the page is recomposing and refetch on 409`。

### Task 5：Filmstrip 整本進度不算有問題的頁

**Files:**
- Modify: `frontend/src/lib/pageState.ts`（新增 `pageDone`）、`frontend/src/features/editor/Filmstrip.tsx`（:60）
- Test: `frontend/src/features/editor/Filmstrip.test.tsx`、`frontend/src/lib/pageState.test.ts`（若存在；否則新增）

**Interfaces:**
- Produces: `export const pageDone = (p: Page) => p.status === "passed" && !hardAttention(p) && !recomposeRunning(p)`。

- [ ] **Step 1：失敗的測試**：:162 的四頁（passed＋failed、passed＋重新合成、passed、passed＋重新合成＋failed）→ `aria-valuenow` "1"、文字「1 / 4 頁完成」（今天是 4）；`pageDone` 單元測試含 review_pose → true。保留 :190-191 的 review_pose 測試。
- [ ] **Step 2：** FAIL → **Step 3：** 實作並在 Filmstrip 使用 → **Step 4：** PASS。
- [ ] **Step 5：** Commit `fix(web): book progress only counts pages that are really done`。

### Task 6：驗收（自己走一次 UI，截圖）

- [ ] 全部測試：後端 pytest、前端 vitest、e2e（照 README），記錄數字。
- [ ] 驗收環境：8001／3001，獨立的 DB 與 storage（絕不用使用者的 `storage-r2/app.db`）。需要 GPU 生成時先問使用者，並和 roboskin-7a 協調。
- [ ] 走一次：做好一頁 → 進階編輯補線畫一筆 → 繪本設定改列印方式 → 畫布筆畫位置正確 → 再畫一筆 → 重新整理後兩筆都在正確位置；另一分頁用舊頁面畫一筆 → 顯示「紙張或列印方式剛改過…」並重新載入；Filmstrip 進度數字。截圖存 `experiments/results/fixes_acceptance_20261118_<HHMM>/`（不進 git）並傳給使用者。
