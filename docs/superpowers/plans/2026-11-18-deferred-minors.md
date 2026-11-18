# 累積小問題修正（第一、二、四類） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉各輪審查留下、仍存在的 10 個小問題（使用者選第一、二、四類；第三類測試補強不做）。

**Architecture:** 各自獨立的小修正，每個都先寫會失敗的測試。不改資料庫結構、不改 API 形狀。

**Tech Stack:** FastAPI、SQLModel、SQLite、pytest；Next.js、React、TypeScript、vitest。

**Spec:** 清單來源：2026-11-18 triage（各輪 SDD ledger 的 deferred／parked 項目逐項對照 main 65b6878）。使用者 2026-11-18 選「1+2+4」。

## Global Constraints

- 分支 `minors-2026-11-18`（從 main 65b6878），不開 worktree。
- 不改 migration、不改 API 回應形狀；錯誤訊息中文。
- `experiments/results/` 不進 git。
- 後端測試 `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/pytest <path> -q -p no:warnings`；前端 `cd frontend && npx vitest run <file>`、`npx tsc --noEmit`、`npx eslint src`。

## Review Focus

1. 角色候選連按：第二個請求要看得到第一個請求寫入的變化語（Task 1 測試以 SQL 執行順序確認變化語是在取得寫入鎖之後才讀）。
2. 生成成功但自動選圖／排流程失敗：頁面最後仍要進流程，不可停在「製作中」（Task 3）。
3. 流程收尾寫入一直失敗：之後的迴圈要再試，不可等到重啟（Task 3）。
4. 文字欄位存好後別處改了文字：要顯示新的值，但打字中（還沒存）不能被蓋掉（Task 2）。
5. 快取上限：少量寫入時不每次掃整個資料夾，但超過上限時仍會刪到上限以下（Task 6）。

---

### Task 1：角色候選的變化語在取得寫入鎖之後才讀
**Files:** `backend/app/api/books.py`（`create_character_candidates` :262 起）；Test: `tests/test_api_books.py`（或角色候選所在的測試檔）
- [ ] 測試 `test_character_variation_is_read_after_the_write_lock`：用 SQLAlchemy `before_cursor_execute` 事件記下 SQL 順序，斷言 `UPDATE books` 出現在讀上一張變化語的 `SELECT ... generations` 之前。→ FAIL
- [ ] 把 `pick_character_variation(..., exclude=_last_character_variation(...))` 與 `qwen_character_prompt` 移到條件式 UPDATE 之後。→ PASS；commit `fix(api): character variation is picked after taking the write lock`

### Task 2：文字欄位存好後跟著伺服器的值
**Files:** `frontend/src/features/editing/usePageText.ts`；Test: 同目錄的 usePageText 測試（沒有就新增 `usePageText.test.tsx`，用 `renderHook`）
- [ ] 測試：改成 "A" → save 成功 → serverValue 變成 "A"（重抓）→ 之後 serverValue 變成 "B" → `value` 是 "B"。另一個：打字 "A" 還沒存時 serverValue 變 "B" → `value` 仍是 "A"。→ FAIL（第一個）
- [ ] 實作：render 時 `edited !== null && edited === serverValue` 就 `setEdited(null)`（伺服器追上了，回到顯示伺服器的值）。→ PASS；commit `fix(web): page text fields follow the server after it caught up`

### Task 3：worker 收尾失敗不卡住頁面
**Files:** `backend/app/services/worker.py`（`_after_success` :448、`_run_page_task` :473-488、`_retry_unfinished`）；Test: `tests/test_worker.py`（或 worker 相關測試檔）
- [ ] 測試 `test_after_success_failure_still_enqueues_page`：monkeypatch `pipeline.on_generation_succeeded` 丟例外 → 頁面仍有排隊的 page task（之後流程照常自動選圖、做完）。
- [ ] 測試 `test_page_task_finish_failure_is_retried_next_loop`：monkeypatch `pipeline.finish_page_task` 前兩次丟例外 → 下一輪 `_retry_unfinished`（或 loop）再寫一次，task 不再是 RUNNING。→ 兩個都 FAIL
- [ ] 實作：`_after_success` 失敗時改用新 session 只呼叫 `pipeline.enqueue_page_task`（生成屬於頁面背景／角色動作、不是候選才排）；`_run_page_task` 兩次都失敗就把 (task_id, status, error) 記進 `self._unfinished_page_tasks`，`_retry_unfinished` 每輪先把它們寫完。→ PASS；commit `fix(worker): a failed follow-up never leaves a page stuck on 製作中`

### Task 4：流程最後一步也確認版面
**Files:** `backend/app/services/page_lineart.py`（抽出 `same_layout(layout)` 條件）、`backend/app/services/pipeline.py`（步驟 5 的 PASSED UPDATE）；Test: `tests/test_pipeline.py`
- [ ] 測試 `test_layout_change_before_pass_reruns`：無缺口的頁（auto 層為空，不會寫 auto 層），在 `ensure_page_lineart` 之後、PASSED 之前改列印方式 → 這次不標 PASSED、重試後以新版面 PASSED（線稿 key 對應新版面）。→ FAIL
- [ ] 實作：`commit_layer` 與步驟 5 共用 `same_layout(layout)`；步驟 5 的 UPDATE 加上這個條件（rowcount 0 → `_Stale`）。→ PASS；commit `fix(pipeline): only mark a page printable for the layout it was computed on`

### Task 5：「主角與故事」改紙張前等修線存好
**Files:** `frontend/src/features/setup/SetupPage.tsx`（`changePaper` :131-145）；Test: `SetupPage.test.tsx`
- [ ] 測試：有一筆修線 PUT 卡住時改紙張 → 放行前沒有 PATCH；修線寫入失敗時改紙張 → 不送 PATCH、toast 說明、紙張選擇回到原值。→ FAIL
- [ ] 實作：`await whenBookWritesIdle(qc, bookId)`（同 BookSettingsDialog），失敗 toast「修線還沒存好…」並 `setPaperDraft(null)`。→ PASS；commit `fix(web): setup waits for line-fix writes before changing paper`

### Task 6：程式整潔
**Files:** `backend/app/storage.py`（刪 `normalize_png` 與只測它的測試；`CachedStorage` 記住大約總量）、`frontend/src/features/fix/fixState.ts` → `frontend/src/lib/fixHref.ts`、`backend/app/services/worker.py`（`run_page_tasks` 的 claim 放進 `asyncio.to_thread`）
- [ ] 測試 `test_cached_storage_does_not_rescan_under_limit`：寫入 20 個小物件（總量遠低於上限）→ `_walk_files` 最多被呼叫 1 次；另保留／確認既有「超過上限會刪到上限以下」的測試。→ FAIL
- [ ] 實作 `CachedStorage`：`self._approx_total: int | None`，第一次需要時掃一次；`_store` 加上新檔大小（覆寫時扣掉舊大小）；只有估計值超過上限才實際掃描並刪，刪完更新估計值。→ PASS
- [ ] 刪除 `normalize_png`（以及 `tests/test_models_storage.py` 只測它的兩個測試）；`fixHref` 移到 `lib/fixHref.ts`（`fixState.ts` 不再 export 它，所有 import 改路徑；測試一起搬）；`run_page_tasks` 的 claim 用 `await asyncio.to_thread(...)`。全部測試 PASS。
- [ ] Commit `refactor: drop dead normalize_png, cheaper cache eviction, fixHref in lib, page-task claim off the event loop`

### Task 7：驗證
- [ ] 後端全部 pytest、前端 vitest＋tsc＋eslint、e2e（dev.sh 開著時從 scratchpad 的前端副本跑）。
