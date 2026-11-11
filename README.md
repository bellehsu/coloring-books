# AI 固定角色兒童著色繪本平台

規格與工作規則見 [CLAUDE.md](CLAUDE.md)；manifest 格式見 [comfy_workflows/README.md](comfy_workflows/README.md)。

## 目錄

```
backend/app/
  config.py                    設定載入（config/settings.yaml + APP_ 環境變數）
  main.py                      FastAPI 進入點、lifespan（DB、worker、啟動 preflight）
  api/books.py                 書籍與角色 endpoints
  api/pages.py                 頁面（建立、排序、更新、刪除）endpoints
  api/compose.py               頁面合成流程：背景（產生／上傳）、角色動作、位置、合成、產生全部素材 endpoints
  api/generations.py           生成紀錄、取消、佇列狀態 endpoints
  api/schemas.py               API 請求／回應 Pydantic 模型
  api/deps.py                  FastAPI 依賴（session、worker）
  models.py                    SQLModel 資料表（書、頁、generation）
  db.py                        SQLite engine 與 session
  storage.py                   生成圖、遮罩、線稿檔案存放；上傳圖片檢查與轉檔
  schemas/manifest.schema.json manifest JSON schema
  services/comfy_client.py     ComfyUI 非同步客戶端
  services/comfy_engine.py     以 ComfyUI 執行生成（實作 engine 介面）
  services/engine.py           生成引擎介面
  services/worker.py           單一背景 worker，依序執行佇列任務
  services/layout.py           版面尺寸計算（五種紙張、兩種列印模式）
  services/prompts.py          提示詞組裝（角色、背景、角色動作）
  services/compose.py          背景＋去背角色合成（Placement、cutout）
  services/lineart_extract.py  生成圖轉線稿（呼叫 lineart_post）
  services/workflow_registry.py workflow 登錄、白名單參數、hash
  services/preflight.py        節點／模型／輸入欄位檢查
  services/lineart_post.py     線稿後處理與指標
comfy_workflows/<name>/        workflow_api.json + manifest.yaml（qwen_character、qwen_background、qwen_edit_pose_cutout）
archive/comfy_workflows/       已停用的 workflow（SDXL、Route B；preflight 不掃描）
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
  invites.py                   邀請連結管理（create／list／rotate／revoke／delete-data）
  storage_gc.py                清理過期的捨棄圖與孤兒物件
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

### 邀請連結與本機開發

- 每個使用者用自己的邀請連結 `/i/{token}` 開啟網站；後端設定 `cb_session` cookie（httpOnly、90 天），
  之後每個請求都檢查 session 與 owner，只看得到自己的繪本（admin 看得到全部）。
- 本機開發不想用連結時，設定 `auth.dev_owner: admin`（`config/settings.yaml`），或在 `scripts/dev.env`
  設 `APP_AUTH__DEV_OWNER=admin`（`dev.env.example` 已預設）：沒有 cookie 的請求就當作 admin。
  已有 admin（例如資料庫遷移時建立的）會直接沿用。
- **對外部署一定不可設 `dev_owner`**（範本預設 `null`）；有設時後端啟動會印出警告。

### 管理邀請與清理儲存空間

兩個腳本都在專案根目錄執行，設定與後端相同（`config/settings.yaml` 或 `APP_SETTINGS_FILE`，`APP_` 環境變數覆寫；
R2 憑證只來自 `APP_STORAGE__R2__ACCESS_KEY`／`SECRET_KEY`）。DB 只存 token 的 hash，連結只在 create／rotate 時印一次。

```bash
PY=$VENV_ROOT/coloring-books/bin/python
env -u PYTHONPATH $PY scripts/invites.py create --name 小明 [--cap 60] [--admin] [--base-url https://網站]
env -u PYTHONPATH $PY scripts/invites.py list                # id、名稱、admin、每日上限、今天（台北）用量、撤銷、session 數；不顯示 token
env -u PYTHONPATH $PY scripts/invites.py rotate --id 1       # 換發新連結（例如遷移時建立、沒人知道 token 的 admin）；舊連結與已登入的瀏覽器失效
env -u PYTHONPATH $PY scripts/invites.py revoke --id 2       # 撤銷，並刪除它的 session（下一個請求就 401）
env -u PYTHONPATH $PY scripts/invites.py delete-data --id 2  # 刪除它的書、頁面、生成紀錄、圖片、session、用量與 owner；要輸入名稱確認（--yes 略過）
env -u PYTHONPATH $PY scripts/storage_gc.py --dry-run        # 只計數：捨棄超過 30 天的圖（--days 調整）與沒有資料列指到的孤兒物件
env -u PYTHONPATH $PY scripts/storage_gc.py                  # 真的刪除（頁面選用中、角色圖不會刪）
```

- 連結前綴：`--base-url`，其次 `auth.invite_base_url`，預設 `http://localhost:3000`。
- 最後一個未撤銷的 admin 要加 `--force-admin` 才能 `delete-data`；還有圖片正在生成時 `delete-data` 會拒絕，稍後再試。
- `storage_gc.py` 不升級資料庫結構：DB 不是最新版時請先啟動一次後端。與 DB 對不上的物件（書／頁／生成紀錄 id 超出 DB、
  或屬於另一本書）不刪；孤兒超過 20 個且超過列出物件的 20% 時整個中止（多半是設定指錯 DB 或 bucket），
  確認無誤才加 `--allow-mass-delete`。
- `storage_gc.py` 的結束碼：0 成功、1 有物件刪除失敗（下次會再試）、2 設定錯誤或 DB 不是最新版、3 孤兒太多而中止。

## 模型（只需要 Qwen）

所有生成（角色、背景、角色動作）都在 ComfyUI 上跑 Qwen-Image-Edit-2511。放在 `comfy.models_root` 底下的子資料夾
（改完 `models_root` 要跑 `scripts/sync_comfy_model_paths.py` 並重啟 ComfyUI）：

| 子資料夾 | 檔名 | 下載頁面 | 用途 |
|---|---|---|---|
| `unet/` | `qwen-image-edit-2511-Q4_K_M.gguf`（13.2 GB） | https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF | 主模型（GGUF，需 ComfyUI-GGUF） |
| `text_encoders/` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI（`split_files/text_encoders/`） | 文字編碼器 |
| `vae/` | `qwen_image_vae.safetensors` | https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI（`split_files/vae/`） | VAE |
| `loras/` | `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning | 4 步加速 LoRA |
| `background_removal/` | `birefnet.safetensors`（0.44 GB） | https://huggingface.co/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors（與 ComfyUI 內建「Remove Background (BiRefNet)」藍圖相同） | 角色動作去背 |

- 自訂節點：[ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)（`UnetLoaderGGUF`；ComfyUI 的 Python 環境要裝 `gguf>=0.13.0`）。
- 以 manifest 為準（`comfy_workflows/*/manifest.yaml` 的 `models`、`custom_nodes`）；`scripts/preflight.py` 會列出缺什麼。
- SDXL、IP-Adapter、ControlNet 的模型已不再需要（舊 workflow 移到 `archive/comfy_workflows/`）。

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
- 佇列為先進先出（依建立時間，最舊的先）；生成紀錄的 `queue_position` 就是實際執行順序。舊版留下、仍在排隊的整頁（`page`）與 SDXL 任務（快速角色動作、細緻背景）會在後端啟動時標為 `cancelled`（錯誤「這種生成已停用」）。
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
- 角色動作：白底上的角色（832×1216）＋同尺寸遮罩（角色處白、其他黑），可以去背、合成。
- 角色候選：白底、黑色粗外框的角色與幾個背景方塊（同樣留一個缺口）。

```bash
$VENV_ROOT/coloring-books/bin/python scripts/dev_fake_backend.py --port 8000 [--storage /tmp/fake-books]
BACKEND_URL=http://127.0.0.1:8000 npm run dev   # 另一個終端機，在 frontend/
```

設定沿用 `config/settings.yaml`（或 `APP_SETTINGS_FILE`），但資料庫與檔案一律放在 `--storage`（預設：新的暫存資料夾），不會碰到正式資料。
啟動時不跑 ComfyUI preflight（`create_app(startup_preflight=False)`）：本機 ComfyUI 開著與否、workflow 能不能跑都不影響它。

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
- `e2e/flow.spec.ts`：建 A4 印刷廠書 → 「產生角色」（一次一張，按鈕改叫「再產生一張」）→ 選它、「用這個角色，開始寫故事 →」→
  故事頁「＋ 新增一頁」、寫場景與中文動作（「抱著一顆紅色的球開心地跳」，檢查動作欄位連到「只寫動作，地點寫在背景…」提示）→
  「產生全部素材（還有 2 張）」→ 縮圖出現、角色動作的 `user_text` 是原本的中文 → 頁面 ① 構圖：素材已自動選用、開頁自動合成 →
  拖曳角色，放開後存位置並自動合成（「✓ 線稿已自動更新」）→ 「下一步：補線 →」→ ② 補線看到 1 個橘色圈 → 「自動補線」→
  「✓ 線條都接好了」→ 「角色看起來對嗎？」按「對」→ 「完成這一頁 ✓」→ 頁面清單標「完成」→ 匯出抽屜下載 PDF（檢查檔頭 `%PDF`）。
  另外：構圖「上傳背景圖」傳 4000×800 的 PNG → 縮圖標「上傳」、舞台的背景已裁成頁面比例；
  故事頁「產生全部素材」後素材還沒好就打開頁面 → worker 自動選用後不用重新整理，舞台填上並「✓ 線稿已自動更新」；
  角色動作自動選用 → 上傳背景圖 → 打開頁面自動合成 → 「下一步：補線 →」看到 ② 補線的完成面板；
  沒設角色時角色動作「換一張」停用，前端資料過期時後端 409「請先設定角色」原樣顯示。
- `e2e/story.spec.ts`：「貼上整本故事」貼 3 頁（第 1 頁 CRLF 換行、全形數字）→ 預覽表 3 列拆對 → 匯入 → 3 列填好 →
  「產生全部素材（還有 6 張）」→ 每頁的背景與角色動作縮圖都出現。
- `e2e/batch.spec.ts`：`POST /books/{id}/materials/batch`（`upload_page_ids` 的頁略過背景、沒場景的頁略過）→ 排入的項目與略過原因 →
  worker 完成後自動選用（只填空的欄位）→ 再呼叫一次不再排。
- `e2e/rwd.spec.ts`：390×844 與 1440×900 開我的繪本、角色、故事（含貼上對話框）、頁面編輯器 ① 構圖與 ② 補線，整頁不可水平捲動。
  390 時：故事每列上下堆疊；頁面清單變成「第 N 頁 / M」下拉選單（用它換頁）；背景與角色動作是舞台下方並排的精簡卡片，點了才展開、可收合；
  舞台照樣可以拖曳角色。1440 時：頁面清單在左、卡片完整顯示。
- `next dev` 同一個 `frontend/` 只能跑一份：`scripts/dev.sh`（或其他 `next dev`）還開著時 e2e 會出現「Another next dev server is already running」，先關掉它再跑。
- 全部約 35 秒（第一次 `next dev` 編譯會再久一點）。

## 內網部署

正式建置、systemd 常駐與 Windows 埠轉發見 [docs/deploy-intranet.md](docs/deploy-intranet.md)（範本在 `deploy/`）。

## 頁面流程（故事 → ① 構圖 → ② 補線）

設定好角色之後：

1. **故事**：每一頁寫場景描述（中文或英文）與角色動作（只寫動作，地點寫在場景；也可以點常用動作），背景可改選「上傳圖片」。
   按「產生全部素材（還有 K 張）」一次排好整本書缺的背景與角色動作（見下方「一次做很多頁時」）；完成的素材會自動選用到空的欄位。
2. **① 構圖**（頁面編輯器）：背景與角色動作各一張卡片，縮圖單選；不滿意就按卡片上的「換一張」，每次產生一張。
   - **背景**（約 20 秒，不用角色參考圖，提示詞要求無人物）：`qwen_background`，Qwen-Image-Edit-2511 GGUF＋Lightning 4 步，不給參考圖；
     每張隨機挑一種構圖變化，且不會和本頁上一張背景相同（再產生時構圖一定換一種）。
     也可以「上傳背景圖」（PNG／JPEG／WebP，10 MB 以內）：置中裁切成書的生成尺寸，存成一筆 `source: "upload"` 的背景並選用。
     只有一種背景風格；Phase 9 以前的舊紀錄（細緻／SDXL）照樣顯示、可以選用。
   - **角色動作**（約 30 秒，832×1216 白底＋去背遮罩）：`qwen_edit_pose_cutout`，Qwen-Image-Edit-2511 GGUF（Q4_K_M）＋Lightning 4 步 LoRA
     以角色參考圖直接編輯出新動作，BiRefNet 去背，只保留遮罩最大的一塊（去掉分離的影子或碎塊）並補滿其中的洞。
     只有一種產生方式；Phase 9 以前的舊紀錄（快速／SDXL）照樣顯示、可以選用。
   - Qwen workflow 缺模型或節點時不會先擋：任務照樣排隊，執行時失敗並在生成紀錄寫出錯誤（`GET /api/system/preflight` 會列出缺少的項目）。
   - **合成**：在舞台上拖曳、拉角落、方向鍵調整角色位置與大小，可水平翻轉；位置自動儲存。放開後（或翻轉、重設、改選背景／角色動作後）
     約 0.6 秒自動合成，不用另外確認：後端用 Pillow 把去背角色貼到背景上，存成一筆 `composite` 生成紀錄並設為本頁
     `selected_generation_id`。這頁補過線時，擺位置後第一次自動合成前會先問「清掉補過的線？」；改選縮圖或上傳背景則直接換、不另外詢問。
3. **② 補線**：xdog 擷取線稿 → 後處理 → 「自動補線」或手動補線 → 「角色看起來對嗎？」按「對」→ 「完成這一頁 ✓」→ 匯出 PDF。

**一次做很多頁時**：到「故事」頁寫好每一頁的場景與角色動作（或選「上傳圖片」當背景），按一次「產生全部素材」，整本書缺的背景與角色動作依頁面順序排好；已有素材、沒有描述、產生中的會略過並在提示中列出。連按兩次、或兩個分頁同時按，也不會重複排隊（後端以鎖序列化）。

背景與角色動作的歷史紀錄每頁各自保存。改選另一張背景或角色動作時，若本頁目前用的是合成圖，會清除選定並退回草稿；
同一組背景、角色、位置再合成時會重用原本的合成圖（補過的線一起回來）。
舊流程的整頁候選（`/pages/{id}/candidates`、`/pages/{id}/select`）與構圖草圖（`/pages/{id}/sketch`、`/books/{id}/reference-sketch`）
在 Phase 9 移除；已選整頁圖的舊頁面照常補線、QA、匯出，也可以用新流程重做：選好背景與角色動作後，舊整頁圖仍是選定圖，合成後換成新的合成圖。

## API 一覽

所有路徑以 `/api` 為前綴。

生成紀錄的 `queue_position` 為 0 表示「下一個／執行中」，必須與 `status` 一起判讀（ComfyUI 離線期間，排在最前面的 queued 任務也會顯示 0）。

| Method | Path | 說明 |
|---|---|---|
| POST | `/books` | 建立繪本 |
| GET | `/books` | 列出繪本 |
| GET | `/books/{book_id}` | 取得單一繪本 |
| PATCH | `/books/{book_id}` | 更新繪本標題、紙張尺寸、列印方式（改尺寸規則見「線稿與 QA」） |
| POST | `/books/{book_id}/character/candidates` | 產生一張角色候選（非同步，202）：每次按一張，body 的 `count` 照收但忽略，回傳只有一筆的清單 |
| POST | `/books/{book_id}/character` | 設定繪本角色 |
| POST | `/books/{book_id}/pages` | 建立頁面 |
| GET | `/books/{book_id}/pages` | 列出頁面 |
| POST | `/books/{book_id}/pages/order` | 調整頁面順序 |
| GET | `/books/{book_id}/generations` | 列出繪本的生成紀錄 |
| PATCH | `/pages/{page_id}` | 更新頁面的 `scene_prompt`、`action_prompt`、`line_detail`、`line_width_px`（舊前端送的 `controlnet_strength` 忽略） |
| DELETE | `/pages/{page_id}` | 刪除頁面（取消排隊中的任務；有執行中的任務時 409） |
| POST | `/pages/{page_id}/backgrounds` | 依頁面場景描述產生一張背景（Qwen，非同步，202）；body 可省略，舊前端送的 `style` 忽略。構圖變化語不重複這頁上一張；生成紀錄的 `background_style` 一律 `"simple"`（Phase 9 以前的舊列可能是 `"detailed"`） |
| POST | `/pages/{page_id}/backgrounds/upload` | 上傳一張圖當本頁背景（multipart 欄位 `file`；PNG／JPEG／WebP、10 MB 以內，最多 6400 萬像素）：置中裁切成生成尺寸，存成成功的背景生成紀錄（`source: "upload"`）並選用（201，回傳 PageOut）；超過像素上限 422「圖片像素太多（最多 6400 萬像素）」，其他不合格 400 |
| POST | `/books/{book_id}/materials/batch` | 產生全部素材（202）：依頁面順序替每頁補排缺的背景與角色動作；選填 body `{"upload_page_ids": [...]}`，這些頁改用上傳圖片，背景略過（reason「改用上傳圖片」）。回傳 `{queued: Generation[], skipped: [{kind: "background" \| "pose", page_id, position, reason}]}` |
| POST | `/pages/{page_id}/poses` | 依 `action` 產生一張角色動作＋遮罩（Qwen，非同步，202；未設角色 409）；舊前端送的 `mode` 忽略。生成紀錄的 `pose_mode` 一律 `"precise"`（Phase 9 以前的舊列可能是 `"fast"`） |
| POST | `/pages/{page_id}/background` | 選定本頁背景（`generation_id`） |
| POST | `/pages/{page_id}/pose` | 選定本頁角色動作（`generation_id`） |
| PUT | `/pages/{page_id}/placement` | 儲存角色位置 `{cx, cy, height, flip}`（中心點與高度為背景的比例） |
| POST | `/pages/{page_id}/compose` | 合成（前端在擺位置、換素材後自動呼叫）：產生／重用 `composite` 生成紀錄並設為選定 |
| GET | `/generations/{gen_id}/mask` | 下載角色動作的去背遮罩 |
| GET | `/generations/{gen_id}/cutout` | 依遮罩去背並裁切的 RGBA PNG（合成預覽用） |
| GET | `/generations/{gen_id}` | 取得生成紀錄（參數、seed、提示詞、耗時、錯誤） |
| GET | `/generations/{gen_id}/image` | 下載生成圖 |
| PATCH | `/generations/{gen_id}` | 捨棄或還原生成結果（使用中的不可捨棄；捨棄排隊中的任務同時會取消它） |
| POST | `/generations/{gen_id}/cancel` | 取消排隊中的任務 |
| GET | `/pages/{page_id}/lineart` | 取得頁面線稿（內容大小 1-bit PNG，有快取） |
| GET | `/pages/{page_id}/qa` | 取得線稿 QA 結果 |
| GET | `/pages/{page_id}/qa/overlay.png` | QA 問題標示疊圖（前端已不使用：補線畫面用 `/qa` 的缺口座標自己畫橘色圈） |
| GET | `/pages/{page_id}/lineart/edits` | 取得修改圖層 |
| PUT | `/pages/{page_id}/lineart/edits` | 儲存修改圖層並重算 QA（已通過的頁面會退回草稿） |
| POST | `/pages/{page_id}/lineart/autofix` | 自動補線縫（最多 3 輪） |
| GET | `/generations/{gen_id}/lineart-preview` | 以指定細緻度／線寬預覽某張生成圖的線稿 |
| POST | `/pages/{page_id}/character-check` | 標記角色是否一致（需帶 `generation_id`，選定圖片已更換則 409） |
| POST | `/pages/{page_id}/pass` | 完成頁面（body 可以是 `{}`；QA 有缺口時後端自動記錄原因「使用者確認完成（N 個缺口）」，也可自帶 `force_reason`） |
| POST | `/pages/{page_id}/unpass` | 取消完成 |
| GET | `/books/{book_id}/export/summary` | 匯出摘要（可匯出頁、略過原因） |
| GET | `/books/{book_id}/export.pdf` | 匯出書本 PDF（`include=passed|all`） |
| GET | `/pages/{page_id}/print.png` | 單頁完整版面 1-bit PNG（300 DPI） |
| GET | `/system/queue` | 佇列狀態（worker 狀態、排隊數、執行中任務）；`pending` 列出排隊中／執行中的角色、背景、角色動作（舊→新），`recent` 是各類型最近 10 筆成功的 Qwen 生成耗時（新→舊，不含上傳），供估算剩餘時間 |
| GET | `/system/preflight` | ComfyUI 連線、模型與節點檢查（並更新快取） |

## 線稿與 QA

- **細緻度與線寬**：每頁有 `line_detail`（`simple`／`standard`／`detailed`，對應 xdog sigma 1.5／1.2／0.8）與 `line_width_px`（300 DPI 像素，8–16，預設 11）。
- **修改圖層**：存在選定生成圖上，格式為筆畫陣列 `{op, width_px, points}`。`op` 為 `draw`（畫黑線）或 `erase`（擦除）；`points` 用相對座標（0–1，相對於線稿內容大小）；`width_px` 以 300 DPI 像素計。換選別張圖就是另一份修改圖層。
- **自動補線**：`POST /pages/{id}/lineart/autofix` 對每個缺口補一筆 `draw`，重算 QA，最多 3 輪；某輪缺口數沒有減少就停止並捨棄該輪。
- **通過（完成）規則**：必須已選圖、角色確認為「一致」（畫面上的「對」）。QA 有錯誤（缺口）也能完成，不必填原因：前端先問一次「還有 N 個缺口…確定完成嗎？」，後端把 `forced_pass_reason` 記成「使用者確認完成（N 個缺口）」（帶了 `force_reason` 就用它）。已通過的頁面在下列情況會無條件退回草稿（值真的有變才算）：修改 `line_detail` 或 `line_width_px`、儲存修改圖層、自動補線、改選另一張圖（同時清除角色確認）；角色標記為不一致也會退回。改紙張尺寸或列印方式則不同：背景重算線稿後，只有新的 QA 有錯誤的頁面才會退回草稿，沒有錯誤的頁面維持已通過。
- **PDF 版面**：頁面依 `position` 排序，預設只匯出已通過的頁（`include=all` 為所有已選圖的頁）。家用（`home`）每頁就是紙張大小，四周留 10 mm 白邊；印刷廠（`print_shop`）每頁為紙張加每邊 3 mm 出血。PDF 本身沒有 CropBox，也沒有裁切標記；裁切框與 5 mm 安全區只出現在 QA 疊圖（`GET /pages/{id}/qa/overlay.png`，前端已不使用）與 BookOut 的版面欄位中。輸出為 1-bit、300 DPI。
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
# Phase 1 的 route_a workflow 已封存到 archive/：跑舊實驗要把 workflows_dir 指過去
APP_PATHS__WORKFLOWS_DIR=archive/comfy_workflows $PY experiments/run_matrix.py run experiments/configs/phase1_example.yaml --dry-run
APP_PATHS__WORKFLOWS_DIR=archive/comfy_workflows $PY experiments/run_matrix.py run experiments/configs/phase1_example.yaml
$PY experiments/run_matrix.py summarize experiments/results/<run_dir>
APP_COMFY__URL=http://127.0.0.1:8190 $VENV_ROOT/coloring-books/bin/uvicorn app.main:app --app-dir backend --port 8000
```

## 資料庫結構變更

資料表結構由 Alembic 管理（`backend/migrations/`，資料庫位置來自 `config/settings.yaml`）。

1. 修改 `backend/app/models.py`。
2. 產生 migration：`cd backend && alembic revision --autogenerate -m "說明"`，並檢查 `migrations/versions/` 裡新產生的檔案（SQLite 以 batch 模式修改資料表）。
3. 後端啟動（`init_db`）時自動升級到最新版；Phase 2 建立的舊資料庫會先標記為 baseline（`0001`）再升級，資料不會遺失。

`tests/test_migrations.py::test_schema_matches_models` 會在忘記產生 migration 時失敗。
