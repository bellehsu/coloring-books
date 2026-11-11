# 專案：AI 固定角色兒童著色繪本平台

## 環境
- OS 與 GPU：RTX 5080（16GB VRAM，Blackwell sm_120）。PyTorch 必須使用 CUDA 12.8+ 版本（cu128 以上）。
  任何安裝 torch 的步驟都要先確認 `torch.cuda.is_available()` 與 `torch.cuda.get_arch_list()` 包含 sm_120。
- ComfyUI 為獨立服務，以 HTTP/WebSocket API 呼叫，不修改其原始碼。
- 後端：Python 3.11+、FastAPI、SQLModel、SQLite。前端：Next.js + React + TypeScript（Phase 4 才開始）。

## 架構原則
- 單一 GPU：生成任務由 FastAPI 內單一背景 worker 依序執行，狀態存在 SQLite。不要引入 Redis、Celery 或其他佇列服務。
- Workflow 模組化：每個 workflow 是一個資料夾，含 `workflow_api.json`（ComfyUI API 格式）與 `manifest.yaml`。
  manifest 定義「邏輯參數 → 節點輸入」對應、參數型別與範圍、需要的模型檔。
  後端只接受 manifest 白名單內的參數，前端與 API 永遠不能直接修改任意節點。
- 節點對應優先用節點 title（`_meta.title`）定位，而不是寫死 node id。
- 頁面與生成紀錄分離：page 底下有多筆 generation，page 只存 `selected_generation_id`。重新生成只新增 generation，不影響其他頁面。
- 每筆 generation 必須保存：workflow 名稱與版本（檔案 hash）、模型檔名、所有參數、seed、提示詞、耗時、錯誤訊息。

## 路徑設定（唯一來源）
- 所有路徑都來自 `config/settings.yaml`，可被環境變數覆寫（前綴 `APP_`）。程式碼中不得寫死路徑。
- 必要設定：`comfy.url`、`comfy.models_root`、`comfy.install_dir`（選填）、`paths.workflows_dir`、`paths.storage_dir`、`paths.database`、`paths.caption_font`（選填，預設 `assets/fonts/NotoSansTC-VF.ttf`）。
- 儲存與身分設定：`storage.backend`（`local`｜`r2`）、`storage.r2.endpoint`、`storage.r2.bucket`、`storage.signed_url_ttl_s`、`storage.cache_dir`、`storage.cache_max_bytes`；
  `auth.dev_owner`、`auth.cookie_secure`、`auth.invite_base_url`。金鑰只能用環境變數：`APP_STORAGE__R2__ACCESS_KEY`、`APP_STORAGE__R2__SECRET_KEY`、
  `APP_STORAGE__SIGNING_KEY`（local 的 HMAC 簽章金鑰），不可寫進設定檔。R2 模式的資料庫要用另一個路徑（例如 `paths.database: storage-r2/app.db`）。
- `scripts/sync_comfy_model_paths.py` 依 settings 產生 ComfyUI 的 `extra_model_paths.yaml`。修改 models_root 後需重啟 ComfyUI。
- 後端啟動與 `GET /api/system/preflight` 時檢查：ComfyUI 是否可連線、每個 workflow 的 manifest 所需模型是否出現在 ComfyUI 的模型清單中、
  所需節點類別是否存在於 `/object_info`。檢查失敗要回報清楚的缺漏清單，不可讓整個服務當機。

## 生成路線（Phase 9 結論：全部使用 Qwen-Image-Edit-2511，2026-11-10）
- 生成全部使用 Qwen-Image-Edit-2511（Q4_K_M GGUF + Lightning 4 步 LoRA，需 ComfyUI-GGUF）：角色參考圖 `qwen_character`（純文字生圖）、
  背景 `qwen_background`（無參考圖）、角色動作 `qwen_edit_pose_cutout`（以角色參考圖編輯出新動作，BiRefNet 去背）；使用者輸入（中文或英文）
  不翻譯，原樣放進英文樣板。頁面以合成產生：背景（Qwen 生成或使用者上傳）＋角色動作 → 後端 Pillow 合成（`composite` generation）
  → xdog 擷取線稿（`app.services.lineart_extract`）。SDXL、IP-Adapter、ControlNet（含構圖草圖）已停用，workflow（`sdxl_text2img`、
  `sdxl_background`、`sdxl_pose_cutout`、`route_a_sdxl_ipadapter_cn`）移到 `archive/comfy_workflows/`（preflight 不掃描）；
  舊流程的頁面（選定圖是整頁 `page` generation）照常補線、匯出，也可以用新流程重做。設計見
  `docs/superpowers/specs/2026-11-10-all-qwen-simplify-design.md`（更早的：`2026-11-06-coloring-book-web-design.md` §10、
  `2026-11-09-page-compose-flow-design.md`）。實驗紀錄：Phase 1 路線比較 `experiments/results/phase1_route_c*`、Phase 5 合成 `phase5_*`、
  Phase 6 Qwen 角色動作 `phase6_qwen_pose_calibration_20261110/`、Phase 7–8 Qwen 背景 `phase7_*`／`phase8_*`、
  Phase 9 全 Qwen 校正與驗收 `phase9_*`。
- 已淘汰：Route A（直接生成黑白線稿）、Route B（以 Qwen-Image-Edit-2511 GGUF 整頁生成線稿）、Route C 的 SDXL 生成（Phase 1–8）。
  `route_b_qwen_edit_2511_gguf` 同樣在 `archive/comfy_workflows/`。
- 生成層要可替換：網頁與 API 只使用邏輯參數；日後雲端 GPU 版本可能改用 diffusers 直接跑模型（目前範圍外，不實作）。

## 線稿後處理（確定性、可測試）
（Route C 先擷取線稿，再進入以下流程）放大到目標尺寸 → 灰階 → 二值化 → 移除小碎片 → 形態學閉運算封閉缺口 → 統一線寬（A4 300 DPI，約 10–12 px）→ 輸出 1-bit PNG。
每個步驟參數化，並計算指標：灰階像素比例、碎片數、封閉區域數、過小區域數、黑色填充比例。

## 工作規則
- 依 Phase 順序進行，每個 Phase 結束時停下來，列出完成項目、測試方式、需要我手動做的事，等我確認再繼續。
- 不要猜 ComfyUI 節點名稱或輸入欄位：一律先查詢執行中 ComfyUI 的 `/object_info` 驗證。
- 不要自行下載模型。需要模型時，列出 Hugging Face 頁面、檔名與應放置的子資料夾，由我下載。
- 每個模組都要有測試；ComfyUI 相關測試要能用 mock 執行，不依賴 GPU。
- 範圍外的功能（帳號系統、付費、雲端 GPU、自動訓練 LoRA）不要實作。
- 實驗紀錄寫在 `experiments/results/`，包含一致性評分、線稿指標、耗時與失敗原因。
