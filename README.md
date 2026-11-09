# AI 固定角色兒童著色繪本平台

規格與工作規則見 [CLAUDE.md](CLAUDE.md)；manifest 格式見 [comfy_workflows/README.md](comfy_workflows/README.md)。

## 目錄

```
backend/app/
  config.py                    設定載入（config/settings.yaml + APP_ 環境變數）
  main.py                      FastAPI 進入點、lifespan（DB、worker、啟動 preflight）
  api/books.py                 書籍與角色 endpoints
  api/pages.py                 頁面、構圖草圖、頁面候選 endpoints
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
  services/prompts.py          提示詞組裝
  services/lineart_extract.py  生成圖轉線稿（呼叫 lineart_post）
  services/workflow_registry.py workflow 登錄、白名單參數、hash
  services/preflight.py        節點／模型／輸入欄位檢查
  services/lineart_post.py     線稿後處理與指標
comfy_workflows/<name>/        workflow_api.json + manifest.yaml（例：sdxl_text2img/）
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
- `npm run e2e`：Playwright 端對端測試（Task 12 補上設定；瀏覽器需另行 `npx playwright install`）。

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
| GET | `/system/preflight` | ComfyUI 連線、模型與節點檢查 |

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
