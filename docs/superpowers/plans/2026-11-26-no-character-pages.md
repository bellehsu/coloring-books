# 不放主角的頁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每頁一個「放主角」設定（預設放）；不放的頁只畫背景（場景文字自動改寫成主體在中間、畫大），只用背景合成、照常線稿與匯出；做好的頁可以來回切換。

**Architecture:** `Page.include_character` 欄位（migration 0017）。改寫規則 `no_character_scene` 後端與前端各一份、共用一份測試案例 JSON。背景工作在不放主角的頁用改寫文字與「在中間」的構圖變化語；produce／頁面流程／合成把「不放主角」當成不需要角色動作。切換已做好的頁走新端點 `POST /api/pages/{id}/character`，要重畫背景時意圖記在那筆背景生成的 request_json（`set_character`），新背景換上時才改欄位。

**Tech Stack:** FastAPI、SQLModel、Alembic（SQLite）、pytest；Next.js／React／TS、TanStack Query、vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-11-26-no-character-pages-design.md`（分支 `no-character-pages-2026-11-26`）

## Global Constraints

- pytest 一律 `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest`（ROS 的 PYTHONPATH 會壞掉收集）。
- 前端：`cd frontend && npx vitest run`、`npx tsc --noEmit`、`npx eslint .`；改了後端 schema 要 `npm run gen:api`（重產 `src/api/openapi.json`、`schema.d.ts`）。
- 不放主角的頁用的使用者文字（中文，照抄）：勾選框「不放主角」（設定頁）／「放主角」（一鍵處理頁、進階編輯）；預覽小字「會畫成：<改寫後文字>」；
  相框頁停用說明「相框頁要放主角」；加回主角的小選擇「重畫背景（恐龍移到旁邊）」（預設）與「保留背景，主角我自己調位置」；
  produce 略過原因「這頁不放主角」。
- 沒勾的頁（`include_character=true`）：提示詞、生成次數、流程與今天完全相同（每個後端任務都要有一個「沒勾的頁不變」的測試）。
- 頁面存的 `scene_prompt` 永遠不被改寫；改寫只發生在組背景提示詞時。
- 不加模型、不加佇列服務；不碰 GPU（GPU 驗收由 controller 另外做，先問 roboskin-71）。
- 不 push；commit 結尾加：
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 與 `Claude-Session: https://claude.ai/code/session_01XPLcrgnVYtdTXYL3AbnBn9`。

## Review Focus

1. 背景還在產生時再切換主角：排隊中的切換被取消、照新請求做；執行中的回 409「這頁正在切換主角，請等一下」；頁面最後的 `include_character` 等於最後一次被接受的操作。（Task 5 測試 `test_toggle_while_background_running`）
2. 切換後按「↶ 復原」：背景與 `include_character` 一起回到切換前。（Task 2 `test_snapshot_round_trips_include_character`、Task 5 `test_undo_after_toggle_off`）
3. 不放主角的頁在設定頁改了場景文字：「會畫成」即時更新；已有 AI 背景時標「要重畫」，重畫用新的改寫文字。（Task 3 `test_redraw_uses_rewritten_scene`、Task 6 `會畫成跟著場景文字更新`）
4. 舊流程整頁圖（selected 是 PAGE generation）的頁、或沒有場景文字的頁按切換：回 409，不排任何工作。（Task 5 `test_toggle_legacy_or_empty_scene_409`）
5. 主題包套用到已有內容的書（只填空欄位）時按總開關：只有預覽裡的頁被設定；已有 AI 背景的頁標「要重畫」、上傳背景的不標。（Task 2 `test_patch_from_setup_marks_redraw`、Task 6 `總開關套用到既有頁`）

---

### Task 1: 改寫規則與「在中間」的構圖變化語

**Files:**
- Create: `backend/app/services/no_character.py`
- Create: `frontend/src/lib/noCharacterScene.ts`、`frontend/src/lib/noCharacterScene.test.ts`
- Create: `frontend/src/lib/noCharacterScene.cases.json`（共用案例）
- Modify: `backend/app/services/prompts.py`（加變化語組與參數）
- Test: `tests/test_no_character.py`、`tests/test_prompts.py`

**Interfaces:**
- Produces: `no_character_scene(scene: str) -> str`（Python）、`noCharacterScene(scene: string): string`（TS，同結果）；
  `QWEN_BACKGROUND_CENTER_VARIATIONS: tuple[str, ...]`；`pick_background_variation(rng, exclude=None, *, center: bool = False) -> str | None`。

- [ ] **Step 1: 寫共用案例 JSON**：`[{"in": ..., "out": ...}, ...]`，至少這些（out 照 spec §5.1 規則手算）：
  - 暴龍「史前時代的草原，畫面右邊有一隻張大嘴巴的暴龍，背景簡單、大塊的形狀、細節少，畫面裡沒有人」→「史前時代的草原，畫面中間有一隻張大嘴巴的暴龍，畫得很大，佔滿大半個畫面，背景簡單、大塊的形狀、細節少，畫面裡沒有人」
  - 迅猛龍「史前時代的森林，畫面右邊有一隻小小的迅猛龍在奔跑」→「史前時代的森林，畫面中間有一隻迅猛龍在奔跑，畫得很大，佔滿大半個畫面」
  - 翼龍（天空句）、蛇頸龍（湖裡）各一；`themePacks.ts` 的 DINOS 第 1 頁原文也放一筆。
  - 沒有左右：「消防局門口，車庫門關著」→ 原樣。空字串 → 空字串。
  - 兩處都有左右（「畫面左邊有一棵樹，畫面右邊有一隻牛」）→ 兩處都變中間、只在第一句後插一次。
- [ ] **Step 2: 寫失敗的測試**：`tests/test_no_character.py::test_shared_cases` 讀 `frontend/src/lib/noCharacterScene.cases.json`（以 repo 根目錄算路徑）逐筆比對；`noCharacterScene.test.ts` 同樣逐筆比對。
  `tests/test_prompts.py::test_center_variations`：`pick_background_variation(rng, center=True)` 只會回 `QWEN_BACKGROUND_CENTER_VARIATIONS` 裡的；連抽兩次排除上一張（`exclude`）時不重複；`center=False` 行為不變。
- [ ] **Step 3: 跑測試確認失敗**：`env -u PYTHONPATH .../python -m pytest tests/test_no_character.py tests/test_prompts.py -q`、`cd frontend && npx vitest run src/lib/noCharacterScene.test.ts` → FAIL（找不到模組）。
- [ ] **Step 4: 實作**：規則照 spec §5.1（替換→拿掉「小小的」→以「，」切句、第一個含「畫面中間」的句後插入「畫得很大，佔滿大半個畫面」）。
  `QWEN_BACKGROUND_CENTER_VARIATIONS = ("The main object is in the middle, large and close up", "The main objects are in the middle, seen from a low angle")`。
- [ ] **Step 5: 跑測試確認通過**（同 Step 3 指令）→ PASS。
- [ ] **Step 6: Commit** `feat: no-character scene rewrite rule (shared cases) and center variations`

### Task 2: 資料欄位、PATCH、PageOut、復原快照

**Files:**
- Create: `backend/migrations/versions/0017_include_character.py`
- Modify: `backend/app/models.py`（Page）、`backend/app/api/schemas.py`（PageUpdate、PageOut）、`backend/app/api/pages.py:update_page`、`backend/app/services/history.py`（snapshot_of、apply_snapshot）
- Test: `tests/test_migrations.py`、`tests/test_api_pages.py`、`tests/test_api_history.py`

**Interfaces:**
- Produces: `Page.include_character: bool`（server_default true）；PATCH 接受 `include_character: StrictBool`；PageOut 回 `include_character: bool`；快照多 `include_character` 鍵（舊快照沒有這個鍵 → 視為 true）。

- [ ] **Step 1: 寫失敗的測試**：
  - `test_migrations.py::test_0017_include_character_defaults_true`：升級後既有頁為 true；降級移除欄位。
  - `test_api_pages.py::test_patch_include_character`：PATCH false → PageOut false；不帶 → 不變；`null` → 422。
  - `test_api_pages.py::test_patch_from_setup_marks_redraw`：`?from_setup=true` 把 include_character 改掉時，有 AI 背景 → `redraw_background=True`；背景是上傳的 → 不標；值沒變 → 不標；沒帶 from_setup → 不標。
  - `test_api_history.py::test_snapshot_round_trips_include_character`：存快照（include true）→ 改成 false → undo → true；舊格式快照（沒有鍵）套用後為 true。
- [ ] **Step 2: 跑測試確認失敗**：`... -m pytest tests/test_migrations.py tests/test_api_pages.py tests/test_api_history.py -q -k "include_character or from_setup_marks"` → FAIL。
- [ ] **Step 3: 實作** migration（照 0016 的寫法，`server_default=sa.true()`）、model、schema、PATCH（include_character 變了算 oneclick 快照條件之一）、快照兩個函式。apply_snapshot 的 include_character 改變要算 `recompose = True`。
- [ ] **Step 4: 跑測試確認通過**（同 Step 2）→ PASS；再跑 `tests/test_api_pages.py tests/test_api_history.py tests/test_migrations.py` 全部 → PASS。
- [ ] **Step 5: Commit** `feat: Page.include_character (migration 0017), PATCH, snapshots`

### Task 3: 背景工作用改寫文字；produce／批次不排角色動作

**Files:**
- Modify: `backend/app/services/jobs.py`（background_job、BookState.plan／has_both_images、build_job）、`backend/app/api/produce.py`（_needed、_redraw_count、plan 次數）、`backend/app/api/compose.py:batch_materials`（若它自己判斷角色）
- Test: `tests/test_api_produce.py`、`tests/test_api_compose.py`、`tests/test_prompts.py`

**Interfaces:**
- Consumes: Task 1 `no_character_scene`、`pick_background_variation(center=)`；Task 2 欄位。
- Produces: `background_job(session, book, page, *, include_character: bool | None = None) -> Job`（None＝用頁面的值；切換端點用 override 畫「切換後」的背景）。
  `NO_CHARACTER_SKIP = "這頁不放主角"`（jobs.py 常數）。

- [ ] **Step 1: 寫失敗的測試**：
  - `test_background_job_no_character`：include false → `gen.prompt` 含改寫文字（含「畫面中間」「畫得很大」）、變化語屬於 CENTER 組；`gen.user_text == page.scene_prompt`（原文）。include true → 與今天相同（prompt 不含「畫得很大」，變化語屬於原本那組）。override 參數優先於頁面值。
  - `test_redraw_uses_rewritten_scene`：不放主角的頁 `redraw_background=True`、改過場景 → 「繼續做」排的背景用新場景的改寫文字。
  - `test_produce_skips_pose_for_no_character`：3 頁、第 2 頁 false → produce 排 3 背景＋2 動作；plan 的 total／fits／estimate_s 按 5 次算；`/produce/plan` 也是 5。第 2 頁的略過原因是 `NO_CHARACTER_SKIP`。
  - `test_produce_unchanged_when_all_include`：全部 true → 次數與今天的固定值相同（拿現有的 produce 測試當基準）。
  - `test_redraw_count_ignores_pose_flag_on_no_character`：不放主角的頁有 `redraw_pose=True` 不算進 redraw_count。
  - batch materials（「產生全部素材」）同樣不排不放主角頁的角色動作。
- [ ] **Step 2: 跑測試確認失敗**：`... -m pytest tests/test_api_produce.py tests/test_api_compose.py tests/test_prompts.py -q -k "no_character or rewritten or unchanged_when_all"` → FAIL。
- [ ] **Step 3: 實作**：`BookState.plan` 在 POSE 的 reason 判斷最前面：`not page.include_character → NO_CHARACTER_SKIP`；`has_both_images` 對不放主角的頁只看背景。
- [ ] **Step 4: 跑測試確認通過**；再跑 `tests/test_api_produce.py tests/test_api_compose.py tests/test_setup_redraw.py tests/test_auto_cleanup.py` 全部 → PASS。
- [ ] **Step 5: Commit** `feat: no-character pages draw rewritten backgrounds and skip poses in produce`

### Task 4: 頁面流程與合成只用背景

**Files:**
- Modify: `backend/app/services/page_compose.py`（compose_page、find_composite／composite_matches／composite_params）、`backend/app/services/pipeline.py:_attempt`、`backend/app/services/history.py`（has_both_slots、can_compose_after 接受 page 的 include_character）、`backend/app/api/review.py`（通過時的角色檢查關卡）、`backend/app/api/pages.py:_on_pipeline`（若它看 pose）
- Test: `tests/test_page_compose.py`、`tests/test_pipeline.py`、`tests/test_api_review.py`

**Interfaces:**
- Produces: 不放主角的頁 `compose_page` 產生一筆 COMPOSITE：圖＝背景原圖（同尺寸、RGB），`composite_params(bg_id, None, placement, None)` 的 pose_id 為 null；
  `find_composite` 能找回 pose 為 null 的既有合成圖；`_attempt` 對不放主角的頁不等角色動作。

- [ ] **Step 1: 寫失敗的測試**：
  - `test_compose_background_only`：include false、只有背景 → 產生 composite，像素等於背景；再呼叫一次重用同一筆；頁面有 pose_generation_id 也忽略它（合成圖與背景相同）。
  - `test_pipeline_passes_without_pose`：include false、只有背景 → run_page_pipeline 回 PASSED、有線稿、`attention` 沒有 review_pose。
  - `test_pipeline_include_true_still_waits`：include true、沒有角色動作 → WAITING（今天的行為）。
  - `test_pass_without_character_check`：不放主角的頁手動「通過」不需要角色檢查。
  - `test_toggle_back_recomposes_with_pose`：同一頁 include 改回 true（有背景、有角色動作）→ 流程產生含角色的新合成圖（與背景不同）。
- [ ] **Step 2: 跑測試確認失敗**：`... -m pytest tests/test_page_compose.py tests/test_pipeline.py tests/test_api_review.py -q -k "background_only or without_pose or still_waits or without_character_check or toggle_back"` → FAIL。
- [ ] **Step 3: 實作**：compose_page 的條件式 UPDATE 用 `pose_generation_id.is_not_distinct_from(...)` 以外，另加 `include_character == page 讀到的值`，切換中途不會用錯的輸入。肖像（frame）分支只在 include true 時走。
- [ ] **Step 4: 跑測試確認通過**；再跑 `tests/test_page_compose.py tests/test_pipeline.py tests/test_pipeline_byte_identity.py tests/test_api_review.py tests/test_compose.py` 全部 → PASS（byte identity 測試證明放主角的頁輸出不變）。
- [ ] **Step 5: Commit** `feat: background-only composite and pipeline for no-character pages`

### Task 5: 切換端點 `POST /api/pages/{id}/character`

**Files:**
- Modify: `backend/app/api/compose.py`（新端點）、`backend/app/api/schemas.py`（`CharacterToggle`）、`backend/app/services/gen_request.py`（`SET_CHARACTER` 旗標與 `set_character_of(gen) -> bool | None`）、`backend/app/services/pipeline.py:_select_new`（take 成功時同交易寫 include_character）
- Test: `tests/test_character_toggle.py`（新）

**Interfaces:**
- Consumes: Task 3 `background_job(..., include_character=)`、`redraw_jobs`／`pose_job`、`take`、`enqueue_jobs`；Task 2 快照。
- Produces: `CharacterToggle(BaseModel): include: StrictBool; redraw_background: StrictBool = True`；回 `PageOut`（202 若有排工作，否則 200）。
  `SET_CHARACTER = "set_character"`（request_json 鍵，值 true/false）。
  錯誤字串常數：`FRAME_NEEDS_CHARACTER = "相框頁要放主角"`、`UPLOAD_NOT_REDRAWN = "上傳的背景不會重畫"`、`TOGGLE_PENDING = "這頁正在切換主角，請等一下"`。
  PageOut 加 `character_pending: bool`（這頁有帶 SET_CHARACTER 的背景在排隊／執行）。

規則（spec §5.3）：
| 情況 | 動作 | 次數 |
|---|---|---|
| include=false，背景是 AI 畫的 | 排背景（override false）帶 TAKE＋SET_CHARACTER=false；欄位等換上時才改 | 1 |
| include=false，背景是上傳的（或沒有背景、`redraw_background=false`） | 立即設 false、push 快照、排頁面流程 | 0 |
| include=true，redraw_background=true，AI 背景 | 排背景（override true）帶 TAKE＋SET_CHARACTER=true；沒有可用角色動作再排 1 個（take） | 1–2 |
| include=true，redraw_background=false | 立即設 true、push 快照；有可用角色動作 → 排頁面流程，沒有 → 排 1 個角色動作（take） | 0–1 |
| include=true，redraw_background=true，上傳背景 | 409 `UPLOAD_NOT_REDRAWN` | — |
| include=false 且位置有 frame | 409 `FRAME_NEEDS_CHARACTER` | — |
| 值與目前相同、且沒有切換中的背景 | 200 不做事 | 0 |

- [ ] **Step 1: 寫失敗的測試**（fake engine，照 `tests/test_background_cleanup.py` 的寫法排隊、跑 worker）：
  `test_toggle_off_redraws_then_flips`（排隊後頁面仍 true；跑完背景換上、include false、合成圖＝新背景）、`test_toggle_off_failure_keeps_page`（背景失敗 → include 仍 true、背景不變、不設 attention failed 以外的東西：照現有失敗規則）、
  `test_toggle_off_cancel_keeps_page`、`test_toggle_on_keep_background_uses_existing_pose`（0 次、立即 true、排流程）、
  `test_toggle_on_keep_background_queues_pose`、`test_toggle_on_redraw_queues_bg_and_pose`（2 次）、`test_upload_background_toggle_off_is_free`、
  `test_upload_redraw_409`、`test_frame_page_409`、`test_toggle_quota_429`（次數不夠：照現有 429 訊息、什麼都沒排）、
  `test_toggle_while_background_running`（這頁有帶 SET_CHARACTER 的背景**排隊中**時再切換 → 取消那筆〔走現有取消路徑、退回次數〕再照新請求處理，最後欄位等於新請求；**執行中**時 → 409 `TOGGLE_PENDING`、什麼都不排）、
  `test_page_out_character_pending`（有帶 SET_CHARACTER 的背景排隊／執行中 → `character_pending=true`，否則 false）、
  `test_undo_after_toggle_off`（換上後 undo → 舊背景、include true）、`test_toggle_legacy_or_empty_scene_409`、`test_editor_toggle_takes_over_user_pick`（不帶 oneclick 也一定換上）。
- [ ] **Step 2: 跑測試確認失敗**：`... -m pytest tests/test_character_toggle.py -q` → FAIL。
- [ ] **Step 3: 實作**端點與 `_select_new` 的 SET_CHARACTER 處理（與 take_generation 同一個交易；take 失敗 rollback 時欄位也不改）。
- [ ] **Step 4: 跑測試確認通過**；再跑整個後端 `... -m pytest -q` → PASS。
- [ ] **Step 5: `cd frontend && npm run gen:api`**，確認 `schema.d.ts` 有 `include_character` 與 `CharacterToggle`；`npx tsc --noEmit` → PASS。
- [ ] **Step 6: Commit** `feat: POST /pages/{id}/character toggles the main character`

### Task 6: 設定頁（③ 故事）總開關、每頁勾選、會畫成、次數

**Files:**
- Modify: `frontend/src/features/setup/storyPreview.ts`（PreviewEntry、needsPose、planCounts）、`StorySection.tsx`（總開關、列勾選、小字）、`SetupPage.tsx`（狀態、開始做時存設定）、`applyImport.ts`（`PagePatch.include_character`、`ImportOptions.includeCharacter`）、`frontend/src/lib/themePacks.ts`（`isFramePlacement` 若需要，或沿用 `@/lib/cleanup` 的 `isPortrait`）
- Test: `storyPreview.test.ts`、`StorySection.test.tsx`、`SetupPage.test.tsx`、`applyImport.test.ts`

**Interfaces:**
- Consumes: Task 1 `noCharacterScene`；Task 5 產生的型別（`Page.include_character`）。
- Produces: `PreviewEntry.includeCharacter: boolean`、`PreviewEntry.characterLocked: boolean`（相框頁）；`needsPose(e)` 在 `!e.includeCharacter` 時回 false。
  `ImportOptions.includeCharacter?: (n: number) => boolean | undefined`（第 n 頁；undefined＝不送）。

- [ ] **Step 1: 寫失敗的測試**：
  - `storyPreview.test.ts`：不放主角的頁不算動作次數（`planCounts` 的 poses、total）。
  - `StorySection.test.tsx`：`總開關勾起來每頁都勾`、`個別取消後總開關是部分（indeterminate）`、`會畫成跟著場景文字更新`（勾了的列顯示「會畫成：」＋改寫結果；沒勾不顯示；上傳背景的列不顯示）、`相框頁勾選框停用並說明`（「相框頁要放主角」）。
  - `applyImport.test.ts`：建立的頁與既有頁 PATCH 帶 `include_character`（只在 includeCharacter 回 boolean 時）。
  - `SetupPage.test.tsx`：`套用恐龍包、勾不放主角、開始做 → 每頁 PATCH include_character=false`、`總開關套用到既有頁`（既有頁 PATCH 帶 `from_setup=true`）。
- [ ] **Step 2: 跑測試確認失敗**：`cd frontend && npx vitest run src/features/setup` → FAIL。
- [ ] **Step 3: 實作**：狀態放 SetupPage（以 entry.key 為鍵的 `Map<string, boolean>`，既有頁初值取 `page.include_character`）；總開關只改沒鎖的列。版面照 spec §5.4 與 AskUserQuestion 的預覽（總開關在標題列按鈕下一行，每列在「AI 畫背景／上傳」右邊）。
- [ ] **Step 4: 跑測試確認通過**；再跑 `npx vitest run`、`npx tsc --noEmit`、`npx eslint .` → PASS。
- [ ] **Step 5: Commit** `feat(web): 不放主角 switch in story setup`

### Task 7: 一鍵處理頁與進階編輯的「放主角」勾選框與小選擇

**Files:**
- Create: `frontend/src/features/editing/CharacterToggle.tsx`（共用：勾選框＋加回主角的小選擇對話框）、`CharacterToggle.test.tsx`
- Modify: `frontend/src/features/fix/fixApi.ts`（`useSetCharacter(bookId, pageId)`，帶 `?oneclick=true`）、`frontend/src/features/fix/FixPage.tsx`（PoseRow 上方放勾選框；不放主角時 PoseRow 換成一行「這頁不放主角」）、`frontend/src/features/editor/ComposeWorkspace.tsx`（PoseCard 頂端放勾選框；不放主角時隱藏動作控制、舞台只畫背景）、相關 module.css
- Test: `FixPage.test.tsx`、`ComposeWorkspace.test.tsx`

**Interfaces:**
- Consumes: Task 5 端點與型別。
- Produces: `<CharacterToggle page={Page} backgroundUploaded={boolean} busy={boolean} onToggle={(body: {include: boolean; redraw_background: boolean}) => Promise<void>} />`。
  取消勾選→直接送 `{include:false, redraw_background:true}`；勾回→開小選擇：「重畫背景（恐龍移到旁邊）」（預設焦點，寫「用 2 次」或「用 1 次」：有可用角色動作時 1 次）、「保留背景，主角我自己調位置」（「用 0 次」或「用 1 次」）；上傳背景只顯示第二個選項。相框頁停用＋「相框頁要放主角」。
  切換中（這頁有帶 set_character 的背景在排隊／執行）勾選框停用並顯示「切換中…」。

- [ ] **Step 1: 寫失敗的測試**：`CharacterToggle.test.tsx`：取消勾選送出的 body、勾回開小選擇（兩個選項文字、預設焦點在第一個、次數文字）、上傳背景只有保留選項、相框停用；
  `FixPage.test.tsx`：`不放主角的頁不顯示角色動作列`、`勾回送 oneclick`；`ComposeWorkspace.test.tsx`：`不放主角時隱藏動作控制`。
- [ ] **Step 2: 跑測試確認失敗**：`npx vitest run src/features/editing/CharacterToggle.test.tsx src/features/fix src/features/editor/ComposeWorkspace.test.tsx` → FAIL。
- [ ] **Step 3: 實作**（Dialog 沿用 `@/components/ui/Dialog`；「切換中」用 `page.character_pending`）。
- [ ] **Step 4: 跑測試確認通過**；`npx vitest run`、`npx tsc --noEmit`、`npx eslint .` → PASS。
- [ ] **Step 5: Commit** `feat(web): 放主角 toggle on the fix page and in the editor`

### Task 8: e2e 與收尾

**Files:**
- Create: `frontend/e2e/no-character.spec.ts`
- Modify: e2e 假後端（若需要；照 `e2e/background-cleanup.spec.ts` 的做法）
- Test: 同上

- [ ] **Step 1: 寫 e2e**：建書（假引擎）→ 套用恐龍包 → 勾「不放主角」→ 開始做 → 等做好 → 打開第 1 頁一鍵處理頁：沒有角色動作列 → 勾回「放主角」→ 選「重畫背景（恐龍移到旁邊）」→ 做好後頁面有角色動作列。
- [ ] **Step 2: 跑**：`cd frontend && npx playwright test e2e/no-character.spec.ts` → PASS（瀏覽器參數照專案設定）。
- [ ] **Step 3: 全部測試**：後端 `... -m pytest -q`、前端 `npx vitest run`、`npx tsc --noEmit`、`npx eslint .`、`npx playwright test` → 全 PASS。
- [ ] **Step 4: Commit** `test(e2e): 不放主角 flow`

（GPU 驗收不在 Task 內：controller 在全部任務與最終審查後，問過 roboskin-71 再用 scratch 伺服器做，見 spec §6。）
