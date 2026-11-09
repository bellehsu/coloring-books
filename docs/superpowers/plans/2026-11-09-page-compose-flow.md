# 頁面合成流程（背景＋角色動作＋合成）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每一頁的生成改成「背景（一次一張）→ 角色新動作（一次一張、自動去背）→ 在背景上擺位置後確認合成」，合成彩圖接上既有的線稿／修線／QA／匯出。

**Architecture:** 兩個新的 ComfyUI workflow（背景：無 IP-Adapter；角色動作：IP-Adapter＋內建去背，輸出角色圖與遮罩）。`Generation.kind` 新增 `background`、`pose`、`composite`；合成由後端以 PIL 確定性產生，寫成一筆 `composite` generation 並設為頁面的 `selected_generation_id`，所以線稿、修線圖層（以 generation 為 key）、QA、匯出都不用改。前端編輯器的模式改為 背景／角色／合成／線稿與修線／QA。

**Tech Stack:** FastAPI、SQLModel、Alembic、Pillow、ComfyUI 0.36.0（@ `config/settings.yaml` 的 `comfy.url`，本機 8188）；Next.js 16、React 19、TanStack Query、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-11-09-page-compose-flow-design.md`（可行性實驗：`experiments/results/phase5_compose_spike_20261109/NOTES.md`）

## Global Constraints

- CLAUDE.md 全部適用：單一 worker；manifest 白名單、節點以 `_meta.title` 定位、節點類別與輸入要用執行中 ComfyUI 的 `/object_info` 驗證；每筆 generation 存 workflow 名稱／版本／hash、模型、參數、seed、提示詞、耗時、錯誤；路徑只來自 settings；不下載模型；每個模組有測試、ComfyUI 測試可 mock；資料表變更要 Alembic migration。
- 背景：**不使用 IP-Adapter**；尺寸＝`compute_layout(book.paper_size, book.print_mode).generation_size`；有草圖時 ControlNet Union `"hed/pidi/scribble/ted"`、end 0.5、強度＝頁面 `controlnet_strength`（0–0.6），沒草圖時強度 0＋白圖（沿用 `route_a` 做法）；一次一張。
- 角色動作：IP-Adapter weight **0.55**、end_at **0.6**；不用 ControlNet；尺寸固定 **832×1216**；提示詞只用角色描述＋動作文字，**不帶場景描述**；去背用內建 `LoadBackgroundRemovalModel`（`bg_removal_name="birefnet.safetensors"`，資料夾 `background_removal`）＋`RemoveBackground`＋`MaskToImage`；一次一張；需要本書已設定角色。
- 動作文字 1–300 字（去空白）；場景描述沿用 0–800 字。
- 合成位置（相對背景）：`cx`、`cy`（角色中心，0–1）、`height`（角色高度／背景高度，0.1–1.0）、`flip`（bool）。預設 `cx=0.5`、`cy=0.66`、`height=0.6`、`flip=false`（中心 0.66、高 0.6 → 底緣 0.96，貼近下方）。
- 選了**不同的**背景或角色動作時：若目前選定圖是 `composite` → 立即清空 `selected_generation_id`、`status=draft`、清 `character_check`／`forced_pass_reason`／`passed_at`；若是舊流程的 `page` 圖 → 不動它（舊頁面照常可用到確認新合成為止）。
- 確認合成時，背景 id＋角色動作 id＋位置（四捨五入到小數 4 位）都相同的既有 `composite` → 直接選回它（修線一起回來），否則新增一筆 `composite`。
- 角色工作室維持一次 4 張，不改。
- 前端：介面文字繁中；只用 `globals.css` 的 token（測試 `tokens.test.ts` 會擋）；RWD 390 px 無水平捲動；位置草稿自動存、依序送出、算進 `hasPendingWrites()`。
- 測試指令：後端 `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`；前端 `cd frontend && npm test && npm run lint && npx tsc --noEmit`；e2e `PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e`。

## Review Focus

1. 已確認合成（甚至已通過）的頁面改選另一張背景 → 選定圖立刻清空、退回草稿；匯出摘要把它列為「還沒選圖」，PDF 不含舊合成圖。（Task 5 `test_select_new_background_clears_composite`）
2. 背景或角色動作還在排隊／失敗、或角色動作沒有遮罩時按「確認合成」→ 409 中文訊息，不產生半成品。（Task 5 `test_compose_requires_ready_inputs`）
3. 角色被拖到一半超出畫面、縮到最小或放到最大 → 合成仍成功，超出部分被裁掉，不會例外。（Task 4 `test_compose_clips_out_of_bounds`）
4. 一邊拖一邊按「確認合成」→ 先送完最後的位置再合成，合成結果等於畫面上的位置。（Task 11 `confirm flushes placement before compose`）
5. 舊流程頁面（選定圖是 `page`）打開新編輯器 → 線稿／QA 分頁仍可用；改選背景不會清掉舊圖。（Task 5 `test_legacy_page_selection_survives_background_pick`、Task 8 `legacy page keeps line/QA tabs`）

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `comfy_workflows/sdxl_background/`（新） | 背景 workflow＋manifest |
| `comfy_workflows/sdxl_pose_cutout/`（新） | 角色動作＋去背 workflow＋manifest（兩個輸出） |
| `backend/app/models.py`、`migrations/versions/0004_*.py`、`storage.py`（改） | 新 kind、遮罩路徑、頁面的背景／動作／位置欄位 |
| `backend/app/services/engine.py`、`comfy_engine.py`、`worker.py`（改） | 新 kind 的路由、多輸出、存遮罩 |
| `backend/app/services/prompts.py`（改） | 背景與角色動作提示詞 |
| `backend/app/services/compose.py`（新） | `Placement`、確定性合成 |
| `backend/app/api/compose.py`（新）、`schemas.py`、`pages.py`、`main.py`（改） | 背景／動作生成與選用、位置、確認合成 |
| `scripts/dev_fake_backend.py`（改） | DemoEngine 支援背景、角色動作＋遮罩 |
| `frontend/src/api/*`（改） | 型別、hooks |
| `frontend/src/features/editor/*`（改／新） | 模式、歷史托盤、背景／角色／合成面板與舞台 |
| `frontend/e2e/*`（改） | 新流程 |

---

### Task 1: 兩個新 workflow

**Files:** Create `comfy_workflows/sdxl_background/{workflow_api.json,manifest.yaml}`、`comfy_workflows/sdxl_pose_cutout/{workflow_api.json,manifest.yaml}`；Test `tests/test_workflow_registry.py`

**Interfaces（Produces）:**
- `sdxl_background` 參數：`prompt`、`negative_prompt`、`seed`、`width`、`height`、`structure_image`（LoadImage）、`controlnet_strength`（0–1，預設 0）、`controlnet_type`（enum，預設 `"hed/pidi/scribble/ted"`）、`controlnet_end_percent`（預設 0.5）、`steps`／`cfg`／`sampler_name`／`scheduler`（同 `sdxl_text2img` 預設）。輸出 title `OUTPUT`。
- `sdxl_pose_cutout` 參數：`prompt`、`negative_prompt`、`seed`、`width`、`height`、`character_image`、`ipadapter_weight`（預設 0.55）、`ipadapter_end_at`（預設 0.6）、取樣參數。輸出兩個：title `OUTPUT`（kind image）與 title `MASK`（kind image，`MaskToImage` 後的 `SaveImage`）。模型清單含 `background_removal/birefnet.safetensors`（`loader: {title: BG_REMOVAL_MODEL, input: bg_removal_name}`）。

- [ ] **Step 1:** 以 `route_a_sdxl_ipadapter_cn/workflow_api.json` 為基礎：背景版移除 IP-Adapter 四個節點（KSampler 的 model 接 CKPT）；角色版移除 ControlNet 五個節點（KSampler 的 positive／negative 接文字編碼），加 `LoadBackgroundRemovalModel`（title `BG_REMOVAL_MODEL`）、`RemoveBackground`（title `REMOVE_BG`，image 接 VAEDecode）、`MaskToImage`（title `MASK_TO_IMAGE`）、`SaveImage`（title `MASK`）。
- [ ] **Step 2:** 用 `curl -s http://127.0.0.1:8188/object_info/<Class>` 驗證每個新節點類別與輸入名稱（只讀）；`scripts/preflight.py`（或 `GET /api/system/preflight`）對兩個 workflow 都是 OK，把輸出貼進報告。
- [ ] **Step 3: tests** `test_repo_compose_workflows_load`：兩個都在 registry、`build_prompt` 用預設值成功、`sdxl_pose_cutout` 的 `output_node_ids` 有 2 個；`ipadapter_weight` 超出範圍會 `ParameterError`。
- [ ] **Step 4:** 全部後端測試 PASS；commit `feat(workflows): background and pose-cutout workflows`

---

### Task 2: 資料表、儲存路徑、提示詞

**Files:** Modify `backend/app/models.py`、`backend/app/storage.py`、`backend/app/services/prompts.py`；Create `backend/migrations/versions/0004_compose_flow.py`；Test `tests/test_models_storage.py`、`tests/test_migrations.py`、`tests/test_prompts.py`

**Interfaces（Produces）:**
- `GenKind.BACKGROUND="background"`、`GenKind.POSE="pose"`、`GenKind.COMPOSITE="composite"`。
- `Generation.mask_path: str | None`。
- `Page.background_generation_id: int | None`、`Page.pose_generation_id: int | None`、`Page.placement_json: str | None`。
- `Storage.generation_mask(book_id, gen_id) -> "books/{b}/generations/{g}_mask.png"`。
- `prompts.py`：`BACKGROUND_STYLE`、`BACKGROUND_NEGATIVE`、`background_prompt(scene: str) -> str`、`POSE_NEGATIVE`、`pose_prompt(character_description: str, action: str) -> str`、`POSE_SIZE = (832, 1216)`。初始文字取自實驗（`experiments/results/phase5_compose_spike_20261109/spike_run.py` 的 `BG_NEG`／`POSE_NEG`／提示詞），背景再加 `"simple uncluttered composition with a few large objects"`；Task 6 校正後可改。

- [ ] **Step 1: tests**：新 kind 可存讀；`test_legacy_db_is_stamped_and_upgraded` 升到 head 後舊資料完好、新欄位為 NULL；`test_schema_matches_models` 仍通過；`background_prompt("a park")` 含 `"a park"` 與 `"no people"`；`pose_prompt` 含角色描述與動作、含 `"white background"`、**不含**場景字樣。
- [ ] **Step 2:** 實作；migration 0004（沿用 0003 的 batch 寫法；enum 以成員名稱存，確認新名稱可寫入）。
- [ ] **Step 3:** 測試 PASS；commit `feat(db): compose-flow generation kinds and page fields`

---

### Task 3: 引擎與 worker 支援新 kind 與多輸出

**Files:** Modify `backend/app/services/engine.py`、`backend/app/services/comfy_engine.py`、`backend/app/services/worker.py`；Test `tests/test_comfy_engine.py`、`tests/test_worker.py`、`tests/fake_engine.py`

**Interfaces:**
- `GenerationResult.extra_images: dict[str, bytes]`（預設空）；角色動作的遮罩放在 `"mask"`。
- `ComfyEngine`：`BACKGROUND_WORKFLOW="sdxl_background"`、`POSE_WORKFLOW="sdxl_pose_cutout"`；`kind=background` → 有 `structure_image` 時上傳並用頁面強度，否則白圖＋強度 0；`kind=pose` → 需要 `character_image`（沒有 → `EngineError("角色動作需要角色參考圖")`）；依 manifest `outputs` 的 title 對應下載：`OUTPUT` → `image`、`MASK` → `extra_images["mask"]`；`kind=pose` 沒有拿到 MASK → `EngineError("去背失敗：沒有遮罩輸出")`。
- worker：成功時若 `extra_images` 有 `"mask"`，寫到 `Storage.generation_mask` 並設 `mask_path`。`encode_request`／`decode_request` 沿用（kind 字串新增值即可）。
- `tests/fake_engine.FakeEngine`：`kind=pose` 時回傳 `extra_images={"mask": <白底黑字 PNG>}`（可設定關閉以測失敗）。

- [ ] **Step 1: tests**：mock ComfyClient 時，background 有／無草圖各送出正確參數（強度、`structure_image`）；pose 送出 IP 0.55／0.6 與尺寸 832×1216、下載兩個輸出；pose 少了 MASK → EngineError；worker 存遮罩並設 `mask_path`。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(engine): background and pose kinds with mask output`

---

### Task 4: 合成服務

**Files:** Create `backend/app/services/compose.py`；Test `tests/test_compose.py`

**Interfaces（Produces）:**
- `@dataclass(frozen=True) class Placement`：`cx: float`、`cy: float`、`height: float`、`flip: bool`；`DEFAULT_PLACEMENT = Placement(0.5, 0.66, 0.6, False)`；`Placement.from_dict(d) / to_dict()`；`validate()`（cx、cy 0–1、height 0.1–1.0，否則 `ValueError`）；`key()` → 四捨五入到 4 位的 tuple。
- `COMPOSE_VERSION = "1"`（演算法改變時 +1）。
- `compose(background: Image.Image, pose: Image.Image, mask: Image.Image, placement: Placement) -> Image.Image`：
  1. 遮罩 > 128 的外框 bbox 裁切角色圖與遮罩（遮罩全空 → `ValueError("遮罩是空的")`）；
  2. 依 `height × 背景高` 等比縮放（LANCZOS），`flip` 時兩者水平翻轉；
  3. 遮罩做 1 px 高斯羽化；
  4. 以 `(cx×W, cy×H)` 為中心貼上，超出背景的部分裁掉；回傳與背景同尺寸的 RGB。

- [ ] **Step 1: tests**：中心像素是角色色、四角是背景色；`flip` 讓左右顛倒；`height=0.6` 時角色像素高度＝背景高的 60%（±2 px）；`test_compose_clips_out_of_bounds`：`cx=0.0`、`cy=1.0`、`height=1.0` 不丟例外且尺寸不變；空遮罩 → ValueError；`Placement.validate` 拒絕 `height=0.05`、`cx=1.2`。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(compose): deterministic background + pose compositing`

---

### Task 5: 合成流程 API

**Files:** Create `backend/app/api/compose.py`；Modify `backend/app/api/schemas.py`、`backend/app/api/pages.py`、`backend/app/main.py`；Test `tests/test_api_compose.py`

**Interfaces（Produces，前綴 `/api`）:**
- `POST /pages/{id}/backgrounds` → 202 `GenerationOut`：用頁面的 `scene_prompt`、草圖（有的話，複製一份快照，同 Phase 2 候選做法）、`controlnet_strength`；`seed` 隨機；尺寸＝繪本生成尺寸。
- `POST /pages/{id}/poses` `{action}` → 202 `GenerationOut`：需要本書已設定角色（否則 409「請先設定角色」）；`user_text`＝動作文字。
- `POST /pages/{id}/background` `{generation_id}`、`POST /pages/{id}/pose` `{generation_id}` → `PageOut`：generation 必須屬於此頁、kind 正確、`succeeded`、未捨棄（pose 還要有 `mask_path`），否則 409；選了不同的 id 時套用 Global Constraints 的清空規則（條件式 UPDATE，同 `select_generation` 寫法）。
- `PUT /pages/{id}/placement` `{cx, cy, height, flip}` → `PageOut`（範圍錯 → 422；不改狀態）。
- `POST /pages/{id}/compose` → `PageOut`：背景與角色動作都已選用且檔案存在，否則 409（訊息：「請先選好背景」／「請先選好角色動作」／「圖片檔案遺失，請重新產生」）；位置用頁面的（沒有就用預設）；同 key 的既有 `composite` 就選回，否則合成、寫檔、新增 `composite` generation（`engine="compose"`、`workflow_name="compose"`、`workflow_version=COMPOSE_VERSION`、`params_json`＝背景 id、角色動作 id、位置、`width`／`height`＝背景尺寸、`seed=0`、`prompt=""`、`status=succeeded`）；設為選定圖片並退回草稿、清角色檢查。
- `select_generation`（pages.py）接受 `kind ∈ {page, composite}`。
- `PageOut` 加 `background_generation_id`、`pose_generation_id`、`placement`（物件或 null）；`GenerationOut` 加 `mask_url`（`/api/generations/{id}/mask`，有遮罩才有）＋新端點 `GET /generations/{id}/mask`。
- 刪除頁面時一併刪遮罩檔。

- [ ] **Step 1: tests**（FakeEngine＋`run_jobs`）：背景／角色動作各產生一張、選用；`test_select_new_background_clears_composite`（合成→通過→改選背景 → `selected_generation_id` None、draft、匯出摘要列為 `no_selection`）；`test_legacy_page_selection_survives_background_pick`；`test_compose_requires_ready_inputs`（未選背景、角色動作排隊中、遮罩檔被刪 → 各自 409）；`test_compose_reuses_identical_composite`（同背景＋動作＋位置第二次確認 → 同一個 generation id、修線還在）；`test_compose_new_placement_new_composite`；位置 422；沒有角色時產生角色動作 → 409「請先設定角色」；`GET /generations/{id}/mask` 回 PNG。
- [ ] **Step 2:** 實作；**Step 3:** 全部後端 PASS；`gen:api` 重產前端型別一起 commit；commit `feat(api): background, pose, placement and compose endpoints`

---

### Task 6: 背景提示詞校正（controller 親自做，GPU）

- [ ] 確認 GPU 空閒後，對公園、廚房、房間三個場景各產生「只寫文字」與「文字＋構圖」背景，比較提示詞變體（至少：Task 2 的預設、再加強「few objects」的版本），以線稿缺口數與目視判斷選一個；更新 `prompts.py` 與測試；結果寫 `experiments/results/phase5_background_prompt_<ts>/NOTES.md`；commit。

---

### Task 7: 前端 API hooks

**Files:** Modify `frontend/src/api/{client.ts,hooks.ts}`；Test `hooks.test.tsx`

**Interfaces:**
- 型別別名沿用；`Placement = {cx: number; cy: number; height: number; flip: boolean}`、`DEFAULT_PLACEMENT`（值同 Global Constraints）。
- hooks：`useGenerateBackground(bookId)`（`{pageId}`）、`useGeneratePose(bookId)`（`{pageId, action}`）、`useSelectBackground(bookId)`、`useSelectPose(bookId)`、`useCompose(bookId)`；查詢沿用 `useGenerations(bookId, {kind: "background"|"pose", pageId, includeDiscarded})`。
- `features/editor/placementSaver.ts`：沿用 `latestByKey` 的每頁依序寫入（`PUT placement`），提供 `savePlacement(pageId, p)`、`whenIdle(pageId)`、`busy()`，並接進 `pendingWrites.hasPendingWrites()`。

- [ ] **Step 1: tests**：各 hook 的路徑與 body；`useCompose` 成功後 invalidate pages、qa、edits、book、exportSummary；placementSaver 連續 5 次只送最新一次、`whenIdle` 在送完後 resolve、失敗時 `hasPendingWrites()` 為 true。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): compose-flow API hooks and placement saver`

---

### Task 8: 編輯器模式、進度清單、歷史托盤

**Files:** Modify `frontend/src/features/editor/{EditorPage,StageHeader,Inspector,ChecklistCard}.tsx`、`frontend/src/lib/checklist.ts`；Create `frontend/src/features/editor/HistoryTray.tsx`（＋ css，取代介面上的 `CandidateTray`）；Test `checklist.test.ts`、`EditorPage.test.tsx`、`HistoryTray.test.tsx`

**行為:**
- `EditorMode = "background" | "pose" | "compose" | "line" | "qa"`；網址 `?mode=` 沿用；預設 `background`。
- 分頁啟用規則：背景永遠可用；角色需要本書已設定角色；合成需要已選背景＋角色動作；線稿與 QA 需要 `selected_generation_id`（舊流程頁面也成立）。停用時說明原因（同 Phase 4）。
- `checklist(page, qa)` 改為 6 項：背景（`background_generation_id`）、角色動作（`pose_generation_id`）、合成（選定圖是 composite → done；是舊流程 page 圖 → done，note「舊流程圖片」；有背景＋動作但未合成 → todo「請確認合成」）、線條封閉、角色外型、通過 QA。
- `HistoryTray({kind, pageId, selectedId, onPick})`：該頁該 kind 的 generations，新的在前；排隊位置／進度／失敗訊息；選用（綠框「使用中」）、捨棄／還原、取消；「歷史 N」切換已捨棄的（沿用 CandidateTray 的行為與測試寫法）。背景模式顯示背景歷史，角色模式顯示角色動作歷史。
- 舊的 `CandidateTray`、`GeneratePanel` 的「產生 4 張候選」不再出現在介面（檔案可刪或改用）。

- [ ] **Step 1: tests**：checklist 各組合（含舊流程頁面）；`legacy page keeps line/QA tabs`；沒有背景時角色／合成停用並有說明；HistoryTray 選用呼叫 `useSelectBackground`／`useSelectPose`。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(editor): compose-flow modes, checklist and history trays`

---

### Task 9: 背景步驟

**Files:** Create `frontend/src/features/editor/{BackgroundStage,BackgroundPanel}.tsx`（＋ css）；Modify `EditorPage.tsx`；Test `BackgroundPanel.test.tsx`

**行為:**
- 舞台：有選用背景時顯示背景圖（點陣舞台上的紙張，同 Phase 4 樣式），上方切換「背景圖／畫構圖」；選「畫構圖」顯示既有 `SketchCanvas`（存檔、`whenIdle`、失敗重試都沿用）。沒有背景時預設顯示畫板與提示「可以只寫場景描述，也可以先畫構圖」。
- 面板：場景描述（沿用 `sentFields` 的送出規則）、構圖強度（0–0.6）、「產生背景」（一次一張；按下前 flush 草圖與欄位，同 Phase 4 的 GeneratePanel 順序；整個流程期間按鈕停用）、預估時間文字。

- [ ] **Step 1: tests**：按「產生背景」→ 先 PUT 草圖、再 PATCH 場景、最後 `POST /backgrounds` 一次；連點只送一次；409 顯示中文訊息。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(editor): background step`

---

### Task 10: 角色動作步驟

**Files:** Create `frontend/src/features/editor/{PoseStage,PosePanel}.tsx`（＋ css）、`frontend/src/lib/actions.ts`；Test `PosePanel.test.tsx`、`actions.test.ts`

**Interfaces:**
- `ACTIONS: {label: string; phrase: string}[]`，十個：站著 `standing`、坐著 `sitting`、走路 `walking`、跑步 `running`、揮手 `waving one hand`、指向前方 `pointing forward`、拿著東西 `holding something with both hands`、跳起來 `jumping happily`、看書 `reading a book`、開心笑 `smiling happily`。

**行為:**
- 面板：動作描述 textarea（1–300 字）；十個動作按鈕（`aria-pressed`），點了把 phrase 加到描述末尾（以「, 」分隔，已存在就不重複）；「產生角色動作」（一次一張；整個流程停用防連點）；未設定角色時停用並連到角色工作室。
- 舞台：顯示選用的角色動作，以遮罩去背後放在棋盤格底上（CSS `mask-image` 用 `mask_url`），讓使用者看出去背是否乾淨；沒有時顯示空狀態。

- [ ] **Step 1: tests**：點「揮手」「看書」→ 描述變成 `waving one hand, reading a book`；再點「揮手」不重複；產生送出 `{action}`；空白描述時按鈕停用。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(editor): pose step with action presets`

---

### Task 11: 合成步驟

**Files:** Create `frontend/src/features/editor/{ComposeStage,ComposePanel}.tsx`（＋ css）、`frontend/src/lib/placement.ts`；Test `placement.test.ts`、`ComposeStage.test.tsx`、`ComposePanel.test.tsx`

**Interfaces:**
- `placement.ts`：`clampPlacement(p) -> Placement`（範圍同 Global Constraints）、`movePlacement(p, dxRel, dyRel)`、`resizePlacement(p, dHeightRel)`、`placementBox(p, poseAspect, stageW, stageH) -> {left, top, width, height}`（px）。
- `ComposeStage({backgroundUrl, poseUrl, maskUrl, poseAspect, placement, onChange})`：背景圖上疊去背角色（CSS `mask-image`）；拖曳角色移動、拖右下角把手等比縮放；鍵盤：方向鍵移動 1%、Shift＋方向鍵 5%、`+`/`-` 縮放 5%（角色框可聚焦、`aria-label`）；每次結束拖曳或按鍵時 `onChange`。
- `ComposePanel`：水平翻轉、重設位置、「確認合成」（先 `placementSaver.whenIdle(pageId)` 再 `useCompose`；整段停用防連點；成功後切到「線稿與修線」）。

- [ ] **Step 1: tests**：`clampPlacement` 邊界；`placementBox` 數值；拖曳後 `onChange` 的 `cx` 增加；方向鍵移動；`confirm flushes placement before compose`（PUT placement 未完成時按確認 → compose POST 在 PUT 之後）；連點只送一次 compose。
- [ ] **Step 2:** 實作；**Step 3:** PASS（含 RWD：舞台寬度跟著容器）；commit `feat(editor): compose step with drag/resize placement`

---

### Task 12: 假後端與 e2e

**Files:** Modify `scripts/dev_fake_backend.py`、`tests/test_scripts.py`、`frontend/e2e/flow.spec.ts`、`frontend/e2e/rwd.spec.ts`

- [ ] DemoEngine：`background` → 畫一個有幾個大物件、其中一個封閉框留 7 px 缺口的背景；`pose` → 白底上的角色（同既有角色圖）＋對應遮罩；`character` 不變。後端測試確認合成後的線稿仍有 ≥1 個缺口、自動補線後為 0。
- [ ] `flow.spec.ts` 改成新流程：建書 → 角色 → 新頁 → 產生背景 → 產生角色動作（點「揮手」）→ 合成：拖曳角色、確認合成 → 線稿看到紅圈 → 自動補線 → QA 一致 → 通過 → 匯出 PDF（`%PDF`）。保留「沒設角色 → 409 中文訊息」測試（改用角色動作端點）。
- [ ] `rwd.spec.ts` 加上合成分頁在 390 px 無水平捲動。
- [ ] `npm run e2e` 全過；commit `test(e2e): compose-flow browser flow`

---

### Task 13: 文件

**Files:** Modify `README.md`、`CLAUDE.md`（「生成路線」一節）、`docs/superpowers/specs/2026-11-06-coloring-book-web-design.md`（§6.3 加一行指向新設計）

- [ ] README：新的頁面流程、API 表新增端點。CLAUDE.md「生成路線」：Route C 改為「背景（無 IP-Adapter）＋角色動作（IP-Adapter＋去背）→ 後端合成 → xdog 擷取」，列出兩個新 workflow；其餘規則不動。commit `docs: compose-flow`

---

### Task 14: 實機驗收（controller 親自做，GPU＋瀏覽器）

- [ ] 用 `scripts/dev.sh`（ComfyUI 8188）走完新流程三個場景各一頁；截圖 1440／390；量每頁操作時間；結果寫 `experiments/results/phase5_compose_ui_check_<ts>/NOTES.md`；commit。

---

## Self-Review 紀錄

- Spec 覆蓋：§2 流程（Task 5、8–11）、§5.1 背景（Task 1、2、5、6、9）、§5.2 角色動作＋常用動作＋不帶場景＋每頁歷史（Task 1–3、5、10）、§5.3 合成＋清空規則＋沿用合成＋位置自動存（Task 4、5、7、11）、§5.4 模式與清單與歷史（Task 8）、§6 資料與 API、舊流程頁面（Task 2、5、8）、§7 成功標準（Task 6、14）、§8 校正（Task 6）。
- 名稱一致：`Placement`（後端 dataclass／前端 type）欄位 `cx/cy/height/flip`；kind 字串 `background/pose/composite`；`mask_url`；`placementSaver.whenIdle`。
