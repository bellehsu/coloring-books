# AI 固定角色兒童著色繪本平台

規格與工作規則見 [CLAUDE.md](CLAUDE.md)；manifest 格式見 [comfy_workflows/README.md](comfy_workflows/README.md)。

## 目錄

```
backend/app/
  config.py                    設定載入（config/settings.yaml + APP_ 環境變數）
  main.py                      FastAPI（目前只有 GET /api/system/preflight）
  schemas/manifest.schema.json manifest JSON schema
  services/comfy_client.py     ComfyUI 非同步客戶端
  services/workflow_registry.py workflow 登錄、白名單參數、hash
  services/preflight.py        節點／模型／輸入欄位檢查
  services/lineart_post.py     線稿後處理與指標
comfy_workflows/<name>/        workflow_api.json + manifest.yaml
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

## 常用指令

```bash
PY=$VENV_ROOT/coloring-books/bin/python

$PY -m pytest -q                                            # 測試
$PY scripts/check_env.py                                    # 環境（ComfyUI 端的 torch/GPU 由 /system_stats 取得）
~/tools/comfy-cli-venv/bin/python scripts/check_env.py      # 用 ComfyUI 的直譯器檢查本機 torch / sm_120
$PY scripts/sync_comfy_model_paths.py --dry-run             # 預覽 extra_model_paths.yaml
$PY scripts/sync_comfy_model_paths.py --install-dir --create-dirs
$PY scripts/preflight.py                                    # 每個 workflow 缺什麼
$PY experiments/run_matrix.py run experiments/configs/phase1_example.yaml --dry-run
$PY experiments/run_matrix.py run experiments/configs/phase1_example.yaml
$PY experiments/run_matrix.py summarize experiments/results/<run_dir>
$VENV_ROOT/coloring-books/bin/uvicorn app.main:app --app-dir backend --port 8000
```
