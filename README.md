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
  services/instance.py         DB 與 storage 的身分標記（storage_instance_id；storage_gc 只在兩邊一致時執行）
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
  dev_fake_backend.py          不需要 GPU 的假生成後端（e2e 用；另有只在這裡才有的 POST /api/_test/invite）
config/settings.example.yaml   設定範本（複製成 settings.yaml）
assets/fonts/                  頁面文字字型 Noto Sans TC（SIL OFL 1.1，見 OFL.txt；路徑設定 paths.caption_font）
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
- **邀請管理頁不接受 `dev_owner` 的後備 admin**：沒有 cookie 的請求即使被當作 admin，`/api/admin/*` 也是 404、首頁沒有「邀請管理」
  （否則區網上任何裝置都能取得每個人的連結）。本機要用邀請管理：載入 dev.env 後執行一次
  `( set -a; . scripts/dev.env; set +a; env -u PYTHONPATH $VENV_ROOT/coloring-books/bin/python scripts/invites.py create --name 自己 --admin )`，
  用印出的管理者連結在瀏覽器開一次（之後有 cookie 就是真正的管理者）。
- **對外部署一定不可設 `dev_owner`**（範本預設 `null`）；有設時後端啟動會印出警告。

### 從 Phase 9 升級（合併後第一次重啟 dev.sh 之前必做）

Phase 10 起本機也用 R2，資料庫與本機資料夾都換一組新的；舊的 `storage/app.db` 與 `storage/books/` 留著當備份、**不再使用**。
後端每次啟動都會把 `paths.database` 指到的資料庫升級到最新版。如果第一次重啟時設定還指著舊的 `storage/app.db`，
它會被**遷移並改寫**：0005 → 0009 刪掉 `page.sketch_path`／`controlnet_strength` 欄位、新增 admin 並把所有書歸給它，
還會在 `storage/books/` 寫入身分標記 `_instance.json`。這些改動無法完整還原，所以順序很重要：

1. 先停掉 dev.sh，**備份整個 `storage/`**：`cp -a storage storage-backup-$(date +%Y%m%d)`。
2. 建立新資料夾：`mkdir storage-r2`（已在 `.gitignore`）。
3. **在第一次重啟之前**，在 `scripts/dev.env` 加上（`dev.env.example` 有同樣的註解範本）：
   ```
   APP_STORAGE__BACKEND=r2
   APP_PATHS__DATABASE=storage-r2/app.db
   APP_PATHS__STORAGE_DIR=storage-r2
   APP_AUTH__DEV_OWNER=admin
   APP_STORAGE__R2__ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
   APP_STORAGE__R2__BUCKET=coloring-books-dev
   APP_STORAGE__R2__ACCESS_KEY=...
   APP_STORAGE__R2__SECRET_KEY=...
   ```
   - `APP_PATHS__STORAGE_DIR` 也要改：R2 模式下圖片在 bucket，但本機快取預設放在 `<paths.storage_dir>/r2_cache/<bucket>`；
     不改的話快取會寫進舊的 `storage/`。相對路徑以專案根目錄為準。
   - R2 帳號、bucket 與憑證的準備見下一節。
4. 重啟前確認設定真的生效（只印路徑，不印憑證）：
   ```bash
   ( set -a; . scripts/dev.env; set +a; env -u PYTHONPATH $VENV_ROOT/coloring-books/bin/python -c \
     'import sys; sys.path.insert(0, "backend"); from app.config import load_settings; s = load_settings(); print(s.storage.backend, s.paths.database, s.paths.storage_dir)' )
   ```
   要看到 `r2 …/storage-r2/app.db …/storage-r2`，才可以執行 `scripts/dev.sh`。
5. 新資料庫是空的（舊的書不搬到 R2）。要再看舊資料，請**複製一份**備份，再用
   `APP_STORAGE__BACKEND=local`、`APP_PATHS__DATABASE=<複製的資料夾>/app.db`、`APP_PATHS__STORAGE_DIR=<複製的資料夾>`
   指過去；不要直接指向 `storage/`。

### R2 準備（Cloudflare R2 物件儲存；只有雲端或想在本機驗證 R2 時才需要）

`storage.backend: local`（預設）不需要以下任何東西。要用 R2，你需要自己建立：

1. Cloudflare 帳號，並在 Dashboard 啟用 R2。
2. 兩個 bucket：開發用 `coloring-books-dev`、正式用 `coloring-books-prod`（兩個環境不要共用，見下方警告）。
3. 每個 bucket 各一個 API token（R2 → Manage API tokens → Object Read & Write，只套用到那個 bucket），記下 Access Key ID 與 Secret Access Key。
4. 每個 bucket 的 CORS 規則：允許網站來源（例如 `http://localhost:3000`、正式網址）的 `GET`（`AllowedHeaders: *`），
   目前圖片只用 `<img>` 載入，不設 CORS 也能顯示；之後若要從瀏覽器直接讀取圖檔內容（canvas、fetch）才需要，先設好避免日後出錯。
5. `scripts/dev.env`（不進版控）加上憑證，或在雲端用環境變數：
   ```
   APP_STORAGE__BACKEND=r2
   APP_STORAGE__R2__ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
   APP_STORAGE__R2__BUCKET=coloring-books-dev
   APP_STORAGE__R2__ACCESS_KEY=...
   APP_STORAGE__R2__SECRET_KEY=...
   ```
   憑證只能用環境變數，寫進 `settings.yaml` 會報錯。`config/settings.example.yaml` 的 `storage` 與 `auth` 區塊有每個欄位的說明。
6. **R2 用另一個資料庫與資料夾：** 把 `paths.database` 改成例如 `storage-r2/app.db`、`paths.storage_dir` 改成 `storage-r2`
   （資料夾先建好；也可用 `APP_PATHS__DATABASE=storage-r2/app.db`、`APP_PATHS__STORAGE_DIR=storage-r2`）。
   R2 的本機快取預設放在 `<paths.storage_dir>/r2_cache/<bucket>`。local 模式的 `storage/` 與 R2 的資料是兩組，不要混用；
   從舊版升級時要在第一次啟動前設好（見上一節）。
7. 第一次啟動後，先載入 dev.env（`set -a; . scripts/dev.env; set +a`）再用 `scripts/invites.py create --name 你的名字 --admin` 建立自己的連結（`dev_owner` 只限本機）；要用「邀請管理」必須用這個管理者連結開一次。
8. 正式環境的 DB 備份可用 `deploy/litestream.yml`（範本，預設不啟用）。

### 管理邀請與清理儲存空間

兩個腳本都在專案根目錄執行，設定與後端相同（`config/settings.yaml` 或 `APP_SETTINGS_FILE`，`APP_` 環境變數覆寫；
R2 憑證只來自 `APP_STORAGE__R2__ACCESS_KEY`／`SECRET_KEY`）。DB 存 token 的 hash（查詢用）與 Fernet 加密版（`owner.token_encrypted`；金鑰見下方「邀請管理頁」），所以管理頁與 `list --show-links` 能再顯示連結。

```bash
PY=$VENV_ROOT/coloring-books/bin/python
set -a; . scripts/dev.env; set +a   # 先載入 dev.env（資料庫、storage、R2 設定）：不載入的話腳本會用 settings.yaml 的路徑，也就是舊的 storage/app.db
env -u PYTHONPATH $PY scripts/invites.py create --name 小明 [--cap 60] [--admin] [--base-url https://網站]
env -u PYTHONPATH $PY scripts/invites.py list                # id、名稱、admin、每日上限、今天（台北）用量、撤銷、session 數；預設不顯示連結（--show-links 多一欄「連結」）
env -u PYTHONPATH $PY scripts/invites.py rotate --id 1       # 換發新連結（例如遷移時建立、沒人知道 token 的 admin）；舊連結與已登入的瀏覽器失效
env -u PYTHONPATH $PY scripts/invites.py revoke --id 2       # 撤銷，並刪除它的 session（下一個請求就 401）
env -u PYTHONPATH $PY scripts/invites.py delete-data --id 2  # 刪除它的書、頁面、生成紀錄、圖片、session、用量與 owner；要輸入名稱確認（--yes 略過）
env -u PYTHONPATH $PY scripts/storage_gc.py --dry-run        # 只計數：捨棄超過 30 天的圖（--days 調整）與沒有資料列指到的孤兒物件
env -u PYTHONPATH $PY scripts/storage_gc.py                  # 真的刪除（頁面選用中、角色圖不會刪）
env -u PYTHONPATH $PY scripts/storage_gc.py --init-instance  # 補上缺少的身分標記（要輸入 storage 名稱確認；--yes 略過）
env -u PYTHONPATH $PY scripts/storage_gc.py --init-instance --adopt-bucket-id  # DB 沒有 id、storage 有標記時，把它寫進 DB
```

- **舊資料庫保護：** 資料庫在 0006 之前（Phase 9）而且已經有書時，後端啟動與 `invites.py` 都會拒絕自動升級，印出提示並結束
  （檔案完全不動）；確定要就地升級時才設 `APP_ALLOW_LEGACY_UPGRADE=1`。沒有書的舊庫照常升級。
- 連結前綴：`--base-url`，其次 `auth.invite_base_url`，預設 `http://localhost:3000`。

**邀請管理頁（管理者）：** 管理者的首頁有「邀請管理」連結 → `/admin/invites`：填名字（1–40 字，未撤銷的邀請不可重名）、管理者、每日上限（1–1000，預設 60）→
「產生連結」，列表（新的在最上面）顯示身分、上限、今天、書數、最後使用、狀態與連結（可隨時再看、按「複製」）。
換發、撤銷、改上限、刪資料仍用 `scripts/invites.py`。非管理者打開這頁與 `/api/admin/*` 都是 404。

- 連結前綴：`auth.invite_base_url`，沒設就用瀏覽器開頁面的網址（尊重 Next proxy 轉發的 `X-Forwarded-Host`／`X-Forwarded-Proto`），
  所以用區網 IP 開管理頁就會得到區網連結。
- 連結金鑰：環境變數 `APP_AUTH__LINK_KEY`（只能放環境變數，不可寫進 settings.yaml），沒設就自動產生 `<paths.storage_dir>/.link_key`（0600）。
  拿到資料庫或 Litestream 備份但沒有金鑰就看不到連結。**這把金鑰要跟資料庫一起保管**：弄丟或更換後，既有連結變成
  「這個連結無法再顯示，請用指令換發」（`invites.py rotate --id N`），邀請本身與已登入的瀏覽器不受影響。
  升級前建立的邀請、`dev_owner` 的 admin 也是同樣顯示（沒有加密的 token）。已撤銷的邀請不顯示連結。
- 最後一個未撤銷的 admin 要加 `--force-admin` 才能 `delete-data`；還有圖片正在生成時 `delete-data` 會拒絕，稍後再試。
- `storage_gc.py` 不升級資料庫結構：DB 不是最新版時請先啟動一次後端。與 DB 對不上的物件（書／頁／生成紀錄 id 超出 DB、
  或屬於另一本書）不刪。孤兒超過 20 個且超過「孤兒＋被參照物件」的 20%，或對不上的物件超過 20 個且超過列出物件的 20%，
  就整個中止、什麼都不刪（多半是設定指錯 DB 或 bucket）；先用 `--dry-run -v --allow-mass-delete` 看清單，確認無誤才加 `--allow-mass-delete`。
- **身分標記：** DB（`app_meta.storage_instance_id`）與 storage（物件 `books/_instance.json`）存同一個隨機 id。
  標記一律直接讀寫 R2（不經過本機快取）。`storage_gc.py` 與 `invites.py delete-data` 要求兩邊都有且一致，
  否則結束碼 4、什麼都不刪、也不改 DB（`--allow-mass-delete` 也不例外）。這個物件永遠不會被 GC 或 `delete-data` 刪掉。
- **後端啟動時的自動建立：** 兩邊都沒有標記時，只有「DB 至少有一本書」或「storage 的 `books/` 是空的」才自動建立
  （新部署、或既有部署升級後第一次啟動）。空的新 DB 指到 `books/` 已經有物件、沒有標記的 storage 時**不會**自動認領：
  只印 `!!!` 警告、照常服務，兩邊都不寫。只有一邊有或兩邊不同時同樣只警告。
- **補標記（復原）：** 先確認 `paths.database` 與 storage 設定指的是同一組，再執行 `storage_gc.py --init-instance`：
  DB 有 id、storage 沒有 → 寫進 storage；兩邊都沒有 → 產生新 id 寫進兩邊。DB 沒有 id、storage 有標記
  （例如降級後再升級、或還原了 0007 之前的備份）→ 加 `--adopt-bucket-id` 把 storage 的 id 寫進 DB。
  都要輸入 storage 名稱確認（R2 是 bucket 名稱，本機是 storage 資料夾的絕對路徑；`--yes` 略過），
  永遠不覆寫已存在的 id（兩邊不同時結束碼 4，請檢查設定）；不能搭配 `--dry-run`。
- **不要把複製的 DB 指到正在使用的 bucket：** 複製（或還原）的 DB 帶著同一個身分標記，GC 與 `delete-data` 會照常執行、
  刪掉另一份 DB 還在用的物件。開發與正式環境各用自己的 bucket（或本機資料夾）。
- **還原舊備份的限制：** 從備份還原的舊 DB 與 bucket 的身分標記相同，GC 會照常執行。這時受影響的只有兩種物件：
  頁面線稿（備份之後重算過的會被當成孤兒刪掉，下次讀取時自動重算）；備份之後才產生的生成圖 id 大於 DB 的
  `sqlite_sequence`，會當成「可能正在寫入」略過、不刪。備份之後新建的書（書號大於 DB 最大的書）也一律略過。
- **GC 與本機快取：** `storage_gc.py` 在另一台機器執行時，不會讓網站主機的磁碟快取失效：被刪的物件可能還留在
  那台的 `<storage.cache_dir>/<bucket>`（快取只是加速，圖片網址仍受權限檢查）。GC 之後請在網站主機刪掉快取資料夾 `<storage.cache_dir>/<bucket>`（預設 `<paths.storage_dir>/r2_cache/<bucket>`），最好在後端停止或閒置時做；重啟後端不會清快取（快取存在磁碟上）。
- `storage_gc.py` 的結束碼：0 成功、1 有物件刪除失敗（下次會再試；`--init-instance` 為取消）、2 設定或參數錯誤、
  DB 不是最新版、3 孤兒太多而中止、4 DB 與 storage 的身分標記不一致（或缺少）、5 讀不到 storage 的身分標記
  （例如 R2 403、逾時、連不上；什麼都沒刪）。`invites.py delete-data` 同樣用 4 與 5。
- R2 的 boto3 client 設了連線逾時 5 秒、讀取逾時 30 秒、最多重試 3 次：R2 連不上時，後端啟動的身分標記檢查約 20 秒後放棄
  （記警告、照常啟動）。R2 的本機快取放在 `<storage.cache_dir>/<bucket>`；舊版放在 `<cache_dir>` 底下的檔案可以直接刪掉。

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

`--dev-owner admin`（預設）：沒有 cookie 的請求當作 admin；`/i/{token}` 照常運作。`POST /api/_test/invite` 只存在於假後端，建立第二個邀請並回傳 token（e2e 隔離測試用，真的 app 沒有）。

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
- `e2e/isolation.spec.ts`：邀請 A 建書；邀請 B（全新的瀏覽器 context 開 `/i/{token}`）書單是空的，開 A 的編輯器網址顯示錯誤（API 404）；
  圖片 API 302 到 `/api/files/...?exp&sig` 後取得 image/png。也確認 A 的 cookie 真的生效（不是沒有 cookie 而被當成 dev admin）：
  有 `cb_session`、preflight 403、書單只有自己的書（沒有 admin 剛建的書）。
- `e2e/invites-admin.spec.ts`：管理者在 `/admin/invites` 產生「小美」→ 列表最上面出現、連結前綴是 3010（經 Next proxy）→ 「複製」→ 剪貼簿 → 全新的 context 開連結，
  首頁沒有書、沒有「邀請管理」、管理頁是 404 → 回管理者重新整理，連結仍在、書 0；390 px 沒有橫向捲動。
- `e2e/invite-links.spec.ts`：無效 token、截斷（`/i`、`/i/`）或多段（`/i/a/b`）的連結都到「連結已失效」頁；
  壞掉的 cookie 到 `/invalid-link?reason=missing`（「請用邀請連結開啟」）。
- 全域錯誤處理（vitest `providers.test.tsx`）：API 回 401 就轉到 `/invalid-link`：detail「請用邀請連結開啟」（這個瀏覽器沒有 session）
  帶 `?reason=missing`，其他（owner 被撤銷）帶 `?reason=revoked`。429 不在全域處理：各呼叫端的錯誤 toast 顯示後端訊息（「今天的生成次數已用完，明天再來」；批次超過剩下的次數時整批不排，訊息是「今天只剩 X 次，這次需要 N 次」）。
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
