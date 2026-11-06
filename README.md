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

## API 一覽

所有路徑以 `/api` 為前綴。

生成紀錄的 `queue_position` 為 0 表示「下一個／執行中」，必須與 `status` 一起判讀（ComfyUI 離線期間，排在最前面的 queued 任務也會顯示 0）。

| Method | Path | 說明 |
|---|---|---|
| POST | `/books` | 建立繪本 |
| GET | `/books` | 列出繪本 |
| GET | `/books/{book_id}` | 取得單一繪本 |
| PATCH | `/books/{book_id}` | 更新繪本標題 |
| POST | `/books/{book_id}/character/candidates` | 產生角色候選（非同步，202） |
| POST | `/books/{book_id}/character` | 設定繪本角色 |
| POST | `/books/{book_id}/pages` | 建立頁面 |
| GET | `/books/{book_id}/pages` | 列出頁面 |
| POST | `/books/{book_id}/pages/order` | 調整頁面順序 |
| GET | `/books/{book_id}/generations` | 列出繪本的生成紀錄 |
| PATCH | `/pages/{page_id}` | 更新頁面的 `scene_prompt` 與 `controlnet_strength` |
| PUT | `/pages/{page_id}/sketch` | 上傳頁面構圖草圖（構圖草圖來源） |
| DELETE | `/pages/{page_id}/sketch` | 移除頁面構圖草圖 |
| GET | `/pages/{page_id}/sketch` | 下載頁面構圖草圖 |
| POST | `/pages/{page_id}/candidates` | 產生頁面候選（非同步，202） |
| POST | `/pages/{page_id}/select` | 選定頁面採用的生成結果 |
| GET | `/generations/{gen_id}` | 取得生成紀錄（參數、seed、提示詞、耗時、錯誤） |
| GET | `/generations/{gen_id}/image` | 下載生成圖 |
| PATCH | `/generations/{gen_id}` | 捨棄或還原生成結果（使用中的不可捨棄；捨棄排隊中的任務同時會取消它） |
| POST | `/generations/{gen_id}/cancel` | 取消排隊中的任務 |
| GET | `/system/queue` | 佇列狀態（worker 狀態、排隊數、執行中任務） |
| GET | `/system/preflight` | ComfyUI 連線、模型與節點檢查 |

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
