# AI 固定角色兒童著色繪本平台

規格與工作規則見 [CLAUDE.md](CLAUDE.md)；manifest 格式見 [comfy_workflows/README.md](comfy_workflows/README.md)。

## 目錄

```
backend/app/
  config.py                    設定載入（config/settings.yaml + APP_ 環境變數）
  main.py                      FastAPI 進入點、lifespan（DB、worker、啟動 preflight）
  api/books.py                 書籍與角色 endpoints
  api/pages.py                 頁面、構圖草圖、頁面候選（舊流程）endpoints
  api/compose.py               頁面合成流程：背景、角色動作、位置、確認合成 endpoints
  api/generations.py           生成紀錄、取消、佇列狀態 endpoints
  api/schemas.py               API 請求／回應 Pydantic 模型
  api/deps.py                  FastAPI 依賴（session、worker）
  models.py                    SQLModel 資料表（書、頁、generation）
  db.py                        SQLite engine 與 session
  storage.py                   生成圖與構圖草圖檔案存放
  schemas/manifest.schema.json manifest JSON schema
  services/comfy_client.py     ComfyUI 非同步客戶端
  services/comfy_engine.py     以 ComfyUI 執行生成（實作 engine 介面）
  services/engine.py           生成引擎介面
  services/worker.py           單一背景 worker，依序執行佇列任務
  services/layout.py           版面尺寸計算（五種紙張、兩種列印模式）
  services/prompts.py          提示詞組裝（角色、背景、角色動作）
  services/compose.py          背景＋去背角色合成（Placement、cutout）
  services/pose_modes.py       角色動作精準／快速模式可用性（看快取的 preflight）
  services/lineart_extract.py  生成圖轉線稿（呼叫 lineart_post）
  services/workflow_registry.py workflow 登錄、白名單參數、hash
  services/preflight.py        節點／模型／輸入欄位檢查
  services/lineart_post.py     線稿後處理與指標
comfy_workflows/<name>/        workflow_api.json + manifest.yaml（sdxl_text2img、route_a_sdxl_ipadapter_cn、sdxl_background、sdxl_pose_cutout、qwen_edit_pose_cutout）
docs/superpowers/
  specs/                       設計規格
  plans/                       實作計畫（各 Phase）
experiments/
  metrics.py                   指標彙總
  run_matrix.py                實驗矩陣
  configs/                     實驗設定
  samples/                     實驗輸入圖
  results/                     實驗結果（不進版控）
scripts/
  check_env.py                 環境檢查
  sync_comfy_model_paths.py    產生 ComfyUI extra_model_paths.yaml
  preflight.py                 命令列 preflight
config/settings.example.yaml   設定範本（複製成 settings.yaml）
storage/                       生成檔案與 SQLite
tests/                         pytest（全部可在無 GPU、無 ComfyUI 下執行）
```

## 安裝

```bash
python3 -m venv $VENV_ROOT/coloring-books
$VENV_ROOT/coloring-books/bin/pip install -e ".[dev]"
cp config/settings.example.yaml config/settings.yaml   # 再依環境修改
```

## 一鍵啟動（開發用）

`scripts/dev.sh` 一次啟動後端與前端（可選 ComfyUI），輸出加上 `[backend]`／`[frontend]`／`[comfy]` 前綴；Ctrl+C 全部關閉，任一服務掛掉也會一起關閉。

```bash
cp scripts/dev.env.example scripts/dev.env   # 第一次：填 PYTHON（專案 venv）、埠號、APP_COMFY__URL、COMFY_START_CMD
scripts/dev.sh                 # 後端 :8000 + 前端 :3000 → 打開 http://localhost:3000
scripts/dev.sh --comfy         # ComfyUI 沒在跑時一起啟動（用 dev.env 的 COMFY_START_CMD；已在跑就略過；GPU 被其他工作占用時不要用）
scripts/dev.sh --backend-only  # 只啟動後端；--frontend-only 只啟動前端
```

- ComfyUI 位址預設讀 `config/settings.yaml` 的 `comfy.url`（8188）；只有要連別的埠時才在 dev.env 設 `APP_COMFY__URL`。
- `scripts/dev.env` 不進版控；同名環境變數優先（例如 `BACKEND_PORT=8001 scripts/dev.sh`）。`--dry-run` 只做檢查並列出會啟動什麼。
- 啟動前會檢查：python 有沒有 uvicorn、前端是否已 `npm ci`、埠號是否被占用。
- 正式（內網）部署請看 [docs/deploy-intranet.md](docs/deploy-intranet.md)，不要用這個腳本。

## 啟動後端

```bash
APP_COMFY__URL=http://127.0.0.1:8190 \
  $VENV_ROOT/coloring-books/bin/uvicorn app.main:app --app-dir backend --port 8000
```

- 後端必須以單一 process 執行（不要用 `uvicorn --workers N`）：生成 worker 在 process 內，且啟動時會把 `running` 的任務重新排隊。
- 生成任務由 FastAPI 內的單一背景 worker 依序執行，狀態存在 SQLite。
- ComfyUI 離線時任務保持 `queued`，不會失敗；`GET /api/system/queue` 的 `state` 會顯示 `engine_unavailable`，ComfyUI 恢復後自動續跑。
- `config/settings.yaml` 預設 `comfy.url` 為 `http://127.0.0.1:8188`；以 `APP_COMFY__URL` 覆寫（8188 若已被其他服務佔用，可改用 8190 等埠）。

## 前端

Next.js（App Router、TypeScript）位於 `frontend/`，需要 Node 24。瀏覽器只呼叫同源 `/api/*`，由 Next rewrites 轉給後端。

```bash
cd frontend
npm install
BACKEND_URL=http://127.0.0.1:8000 npm run dev   # 前端 http://localhost:3000；後端需先在 8000 啟動
npm run build && npm test && npm run lint
```

- `BACKEND_URL`：後端位址，預設 `http://127.0.0.1:8000`（`next.config.ts` 的 rewrites 與 build 時讀取）。
- `npm run gen:api`：由後端 OpenAPI 重新產生 `src/api/openapi.json` 與 `src/api/schema.d.ts`。**後端 API 的路由或 schema 有變動時必須重跑並一起 commit。**
  Python 以環境變數 `PYTHON` 指定（預設 `python`），指令內已含 `env -u PYTHONPATH`（ROS 的 PYTHONPATH 會破壞載入）：

  ```bash
  PYTHON=$VENV_ROOT/coloring-books/bin/python npm run gen:api
  ```

### 假生成後端（不需要 GPU）

`scripts/dev_fake_backend.py` 是真正的 FastAPI 後端，但生成引擎換成 `DemoEngine`：每張圖延遲 0.3 秒、回報 3 次進度，用 Pillow 畫彩色圖：
- 背景：太陽、雲、樹、草叢幾個大物件，上方一個不填色的大框，上緣刻意留了約 7 px 的缺口（A4 300 DPI 線稿上約 20 px）。
  物件都避開角色的預設位置，合成後 QA 一定會報 1 個「線條有缺口」，按「自動補線」後缺口歸零。
- 角色動作：白底上的角色（832×1216）＋同尺寸遮罩（角色處白、其他黑），可以去背、合成。精準／快速兩種模式畫法相同。
- 角色候選（與舊流程的頁面圖）：白底、黑色粗外框的角色與幾個背景方塊（同樣留一個缺口）。

```bash
$VENV_ROOT/coloring-books/bin/python scripts/dev_fake_backend.py --port 8000 [--storage /tmp/fake-books]
BACKEND_URL=http://127.0.0.1:8000 npm run dev   # 另一個終端機，在 frontend/
```

設定沿用 `config/settings.yaml`（或 `APP_SETTINGS_FILE`），但資料庫與檔案一律放在 `--storage`（預設：新的暫存資料夾），不會碰到正式資料。
啟動時不跑 ComfyUI preflight（`create_app(startup_preflight=False)`）：本機 ComfyUI 開著與否、workflow 能不能跑都不影響它，
`GET /api/system/pose-modes` 一律回報精準／快速都可用（e2e 要測「精準不可用」時在瀏覽器端攔截這個 API）。

### 端對端測試（Playwright）

```bash
cd frontend
npx playwright install chromium        # 第一次（或升級 @playwright/test 後）
PYTHON=$VENV_ROOT/coloring-books/bin/python npm run e2e
```

- 這台機器的 venv 是 `/ai-data/venvs/ubuntu24.04/coloring-books`（`VENV_ROOT=/ai-data/venvs/ubuntu24.04`）。`PYTHON` 預設 `python`；指令內已含 `env -u PYTHONPATH`。
- `playwright.config.ts` 會自動啟動兩個服務：假後端（`127.0.0.1:8010`，每次執行用新的暫存 storage）與 `next dev -p 3010`（`BACKEND_URL=http://127.0.0.1:8010`）。
  非 CI 時若這兩個埠已有服務就直接重用（資料會累積；測試不依賴既有資料）。
- 只用 chromium，並以 `--disable-gpu --disable-software-rasterizer` 啟動：WSL2 上 headless chromium 的 GPU 行程常卡住，`requestAnimationFrame` 不觸發、點擊一直等不到。
- `e2e/flow.spec.ts`：建 A4 印刷廠書 → 產生並設定角色 → 新增頁、在畫板畫方框 → 產生背景並選用 →
  點「揮手」，產生方式預設「精準」產生一張（送出 `mode:"precise"`、縮圖標「精準」），切到「快速」再產生一張（標「快速」），選用精準那張 →
  合成：拖曳＋方向鍵移動角色後立刻「確認合成」（位置 PUT 故意變慢，檢查合成在最後一次位置寫完後才送出）→ 自動切到線稿、看到紅圈 →
  自動補線後缺口 0 → QA 一致 → 標為通過 → 匯出抽屜下載 PDF（檢查檔頭 `%PDF`）；
  以及沒設角色時角色分頁停用、後端 409「請先設定角色」原樣顯示；
  `/api/system/pose-modes` 回報精準不可用時「精準」停用並顯示「精準模式缺少：…」、改選「快速」，產生時送出 `mode:"fast"`。
- `e2e/rwd.spec.ts`：390×844 與 1440×900 開我的繪本、角色工作室、頁面編輯器（背景、畫構圖、角色、線稿模式）與合成分頁（角色移到邊緣外也一樣），
  整頁不可水平捲動；390 時頁面清單 `overflow-x: auto`（在清單內捲動）、檢查面板在工作區下方。
- `next dev` 同一個 `frontend/` 只能跑一份：`scripts/dev.sh`（或其他 `next dev`）還開著時 e2e 會出現「Another next dev server is already running」，先關掉它再跑。
- 全部約 30 秒（第一次 `next dev` 編譯會再久一點）。

## 內網部署

正式建置、systemd 常駐與 Windows 埠轉發見 [docs/deploy-intranet.md](docs/deploy-intranet.md)（範本在 `deploy/`）。

## 頁面流程（Phase 5：背景＋角色動作＋合成）

設定好角色之後，每一頁依序做：

1. **背景**：輸入場景描述，按「產生背景」，每次一張（`sdxl_background`，不用 IP-Adapter，提示詞要求無人物、少量大物件）。
   畫構圖（ControlNet scribble）為選用；不畫就不使用 ControlNet。
2. **角色動作**：輸入動作文字或點常用動作按鈕（揮手、跑步…），選「產生方式」後按「產生角色動作」，每次一張，832×1216 白底＋去背遮罩。
   - **精準**（預設，約 30 秒，角色最像、動作最準）：`qwen_edit_pose_cutout`，Qwen-Image-Edit-2511 GGUF（Q4_K_M）＋Lightning 4 步 LoRA
     以角色參考圖直接編輯出新動作，需要 ComfyUI-GGUF；BiRefNet 去背。
   - **快速**（約 10 秒，可能和角色有落差）：`sdxl_pose_cutout`，角色參考圖 IP-Adapter 0.55，ComfyUI 內建 `RemoveBackground` 產生遮罩。
   - 精準是預設；選擇記在瀏覽器（localStorage `coloring.poseMode`）。後端快取的 preflight 判定精準不可用（例如沒裝 ComfyUI-GGUF、缺 GGUF 檔）時，
     「精準」停用並顯示缺少的項目，自動改用快速（退路，不改寫記住的選擇）。歷史縮圖左上角標「精準」／「快速」（Phase 5 的舊資料算快速）。
     兩種模式產生的角色動作都只保留遮罩最大的一塊（去掉分離的影子或碎塊）。
3. **合成**：拖曳、拉角落、方向鍵調整角色位置與大小，可水平翻轉；位置自動儲存。按「確認合成」後，後端用 Pillow
   把去背角色貼到背景上，存成一筆 `composite` 生成紀錄並設為本頁 `selected_generation_id`。
4. **線稿、QA、匯出**：與之前相同（xdog 擷取 → 後處理 → 補線 → 角色一致確認 → 通過 → PDF）。

背景與角色動作的歷史紀錄每頁各自保存。改選另一張背景或角色動作時，若本頁目前用的是合成圖，會清除選定並退回草稿；
同一組背景、角色、位置再按確認會重用原本的合成圖。舊流程的整頁候選（`/pages/{id}/candidates`）仍保留，已選的舊頁面不受影響。

## API 一覽

所有路徑以 `/api` 為前綴。

生成紀錄的 `queue_position` 為 0 表示「下一個／執行中」，必須與 `status` 一起判讀（ComfyUI 離線期間，排在最前面的 queued 任務也會顯示 0）。

| Method | Path | 說明 |
|---|---|---|
| POST | `/books` | 建立繪本 |
| GET | `/books` | 列出繪本 |
| GET | `/books/{book_id}` | 取得單一繪本 |
| PATCH | `/books/{book_id}` | 更新繪本標題、紙張尺寸、列印方式（改尺寸規則見「線稿與 QA」） |
| POST | `/books/{book_id}/character/candidates` | 產生角色候選（非同步，202） |
| POST | `/books/{book_id}/character` | 設定繪本角色 |
| POST | `/books/{book_id}/pages` | 建立頁面 |
| GET | `/books/{book_id}/pages` | 列出頁面 |
| POST | `/books/{book_id}/pages/order` | 調整頁面順序 |
| GET | `/books/{book_id}/generations` | 列出繪本的生成紀錄 |
| PATCH | `/pages/{page_id}` | 更新頁面的 `scene_prompt`、`controlnet_strength`、`line_detail`、`line_width_px` |
| PUT | `/pages/{page_id}/sketch` | 上傳頁面構圖草圖（構圖草圖來源） |
| DELETE | `/pages/{page_id}/sketch` | 移除頁面構圖草圖 |
| GET | `/pages/{page_id}/sketch` | 下載頁面構圖草圖 |
| POST | `/pages/{page_id}/candidates` | 產生頁面候選（非同步，202） |
| POST | `/pages/{page_id}/backgrounds` | 產生一張背景（非同步，202；可用構圖草圖） |
| POST | `/pages/{page_id}/poses` | 依 `action` 產生一張角色動作＋遮罩；`mode`：`"precise"`（預設，Qwen）或 `"fast"`（SDXL），其他值 422（非同步，202；未設角色 409；精準不可用 503「精準模式缺少：…」） |
| POST | `/pages/{page_id}/background` | 選定本頁背景（`generation_id`） |
| POST | `/pages/{page_id}/pose` | 選定本頁角色動作（`generation_id`） |
| PUT | `/pages/{page_id}/placement` | 儲存角色位置 `{cx, cy, height, flip}`（中心點與高度為背景的比例） |
| POST | `/pages/{page_id}/compose` | 確認合成：產生／重用 `composite` 生成紀錄並設為選定 |
| GET | `/generations/{gen_id}/mask` | 下載角色動作的去背遮罩 |
| GET | `/generations/{gen_id}/cutout` | 依遮罩去背並裁切的 RGBA PNG（合成預覽用） |
| POST | `/pages/{page_id}/select` | 選定頁面採用的生成結果 |
| GET | `/generations/{gen_id}` | 取得生成紀錄（參數、seed、提示詞、耗時、錯誤） |
| GET | `/generations/{gen_id}/image` | 下載生成圖 |
| PATCH | `/generations/{gen_id}` | 捨棄或還原生成結果（使用中的不可捨棄；捨棄排隊中的任務同時會取消它） |
| POST | `/generations/{gen_id}/cancel` | 取消排隊中的任務 |
| GET | `/pages/{page_id}/lineart` | 取得頁面線稿（內容大小 1-bit PNG，有快取） |
| GET | `/pages/{page_id}/qa` | 取得線稿 QA 結果 |
| GET | `/pages/{page_id}/qa/overlay.png` | QA 問題標示疊圖 |
| GET | `/pages/{page_id}/lineart/edits` | 取得修改圖層 |
| PUT | `/pages/{page_id}/lineart/edits` | 儲存修改圖層並重算 QA（已通過的頁面會退回草稿） |
| POST | `/pages/{page_id}/lineart/autofix` | 自動補線縫（最多 3 輪） |
| GET | `/generations/{gen_id}/lineart-preview` | 以指定細緻度／線寬預覽某張生成圖的線稿 |
| POST | `/books/{book_id}/reference-sketch` | 由圖片擷取參考草圖 |
| POST | `/pages/{page_id}/character-check` | 標記角色是否一致（需帶 `generation_id`，選定圖片已更換則 409） |
| POST | `/pages/{page_id}/pass` | 通過頁面（可帶 `force_reason` 強制通過） |
| POST | `/pages/{page_id}/unpass` | 取消通過 |
| GET | `/books/{book_id}/export/summary` | 匯出摘要（可匯出頁、略過原因） |
| GET | `/books/{book_id}/export.pdf` | 匯出書本 PDF（`include=passed|all`） |
| GET | `/pages/{page_id}/print.png` | 單頁完整版面 1-bit PNG（300 DPI） |
| GET | `/system/queue` | 佇列狀態（worker 狀態、排隊數、執行中任務） |
| GET | `/system/preflight` | ComfyUI 連線、模型與節點檢查（並更新快取） |
| GET | `/system/pose-modes` | 角色動作精準／快速是否可用 `{precise, fast: {available, missing}}`（只讀快取的 preflight，不連 ComfyUI；ComfyUI 連不上或尚未檢查時都算可用） |

## 線稿與 QA

- **細緻度與線寬**：每頁有 `line_detail`（`simple`／`standard`／`detailed`，對應 xdog sigma 1.5／1.2／0.8）與 `line_width_px`（300 DPI 像素，8–16，預設 11）。
- **修改圖層**：存在選定生成圖上，格式為筆畫陣列 `{op, width_px, points}`。`op` 為 `draw`（畫黑線）或 `erase`（擦除）；`points` 用相對座標（0–1，相對於線稿內容大小）；`width_px` 以 300 DPI 像素計。換選別張圖就是另一份修改圖層。
- **自動補線**：`POST /pages/{id}/lineart/autofix` 對每個缺口補一筆 `draw`，重算 QA，最多 3 輪；某輪缺口數沒有減少就停止並捨棄該輪。
- **通過規則**：必須已選圖、角色確認為「一致」，且 QA 沒有錯誤；有錯誤時需填 `force_reason` 才能強制通過。已通過的頁面在下列情況會無條件退回草稿（值真的有變才算）：修改 `line_detail` 或 `line_width_px`、儲存修改圖層、自動補線、改選另一張圖（同時清除角色確認）；角色標記為不一致也會退回。改紙張尺寸或列印方式則不同：背景重算線稿後，只有新的 QA 有錯誤的頁面才會退回草稿，沒有錯誤的頁面維持已通過。
- **PDF 版面**：頁面依 `position` 排序，預設只匯出已通過的頁（`include=all` 為所有已選圖的頁）。家用（`home`）每頁就是紙張大小，四周留 10 mm 白邊；印刷廠（`print_shop`）每頁為紙張加每邊 3 mm 出血。PDF 本身沒有 CropBox，也沒有裁切標記；裁切框與 5 mm 安全區只出現在 QA 疊圖（`GET /pages/{id}/qa/overlay.png`）與 BookOut 的版面欄位中。輸出為 1-bit、300 DPI。
- **改尺寸規則**：`PATCH /books/{id}` 只能改成同比例群組的紙張（A4／A5／B5 同組；Letter 與 SQUARE_21 各自獨立），否則 422。改紙張或列印方式後，背景會重算所有已選圖頁面的線稿，修改圖層（相對座標）沿用；已通過的頁面只有在新 QA 有錯誤時才退回草稿。缺口偵測以像素計算，換尺寸後可能出現新的缺口（實測 A4→A5 多出 19 個），因此改尺寸後請重新檢查 QA 並再跑自動補線。

## 常用指令

```bash
PY=$VENV_ROOT/coloring-books/bin/python

env -u PYTHONPATH $PY -m pytest -q                              # 測試（本機 ROS 的 PYTHONPATH 會讓 pytest 收集失敗，需 unset）
$PY scripts/check_env.py                                    # 環境（ComfyUI 端的 torch/GPU 由 /system_stats 取得）
~/tools/comfy-cli-venv/bin/python scripts/check_env.py      # 用 ComfyUI 的直譯器檢查本機 torch / sm_120
$PY scripts/sync_comfy_model_paths.py --dry-run             # 預覽 extra_model_paths.yaml
$PY scripts/sync_comfy_model_paths.py --install-dir --create-dirs
$PY scripts/preflight.py                                    # 每個 workflow 缺什麼
$PY experiments/run_matrix.py run experiments/configs/phase1_example.yaml --dry-run
$PY experiments/run_matrix.py run experiments/configs/phase1_example.yaml
$PY experiments/run_matrix.py summarize experiments/results/<run_dir>
APP_COMFY__URL=http://127.0.0.1:8190 $VENV_ROOT/coloring-books/bin/uvicorn app.main:app --app-dir backend --port 8000
```

## 資料庫結構變更

資料表結構由 Alembic 管理（`backend/migrations/`，資料庫位置來自 `config/settings.yaml`）。

1. 修改 `backend/app/models.py`。
2. 產生 migration：`cd backend && alembic revision --autogenerate -m "說明"`，並檢查 `migrations/versions/` 裡新產生的檔案（SQLite 以 batch 模式修改資料表）。
3. 後端啟動（`init_db`）時自動升級到最新版；Phase 2 建立的舊資料庫會先標記為 baseline（`0001`）再升級，資料不會遺失。

`tests/test_migrations.py::test_schema_matches_models` 會在忘記產生 migration 時失敗。
