# Phase 2 後端核心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓後端可以建立繪本、產生並設定角色、管理頁面與構圖草圖、排隊產生頁面候選圖，並保存完整生成紀錄——全部透過 HTTP API，生成由單一背景 worker 經 ComfyUI 執行。

**Architecture:** FastAPI + SQLModel（SQLite）。API 只寫資料庫（建立 `queued` 的 generation），單一 asyncio 背景 worker 依序取出、呼叫「生成引擎介面」產圖、存檔、更新狀態。第一版只有 ComfyUI 引擎，它把邏輯參數對應到 manifest 白名單參數；日後可換成 diffusers 引擎而不動 API。

**Tech Stack:** Python 3.12、FastAPI 0.146、SQLModel 0.0.48、SQLite、httpx、Pillow、pytest + pytest-asyncio；新增依賴 `python-multipart`（檔案上傳）。

**Spec:** `docs/superpowers/specs/2026-11-06-coloring-book-web-design.md`（本計畫實作 §11 的 Phase 2，以及 Phase 2 需要用到的 §6.1 版面計算與 §10 生成設定）

## Global Constraints

- 遵守 `CLAUDE.md`：單一背景 worker、狀態存 SQLite、不引入 Redis／Celery；manifest 白名單；節點以 `_meta.title` 定位；page 只存 `selected_generation_id`，重新生成只新增 generation。
- 每筆 generation 保存：workflow 名稱、版本、hash、模型檔名、所有參數、seed、提示詞、耗時、錯誤訊息。
- 路徑只來自 `config/settings.yaml`（`paths.storage_dir`、`paths.database`、`paths.workflows_dir`）；資料庫內存「相對於 storage_dir 的路徑」。
- 測試一律可在無 GPU、無 ComfyUI 下跑；執行方式：`env -u PYTHONPATH $VENV_ROOT/coloring-books/bin/python -m pytest -q`（本機 ROS 的 PYTHONPATH 會讓 pytest 收集失敗）。
- 不自行下載模型。新 workflow 只用已存在的 `sd_xl_base_1.0.safetensors`。
- 不猜節點：新 workflow 的節點類別與輸入必須用執行中 ComfyUI 的 `/object_info` 驗證（Task 4 的手動步驟）。
- 頁面生成預設（spec §10）：IP-Adapter weight `0.55`、end_at `0.6`；ControlNet type `"hed/pidi/scribble/ted"`、end_percent `0.5`；構圖強度預設 `0.4`、允許範圍 `0.0–0.6`；steps 30、cfg 6、dpmpp_2m、karras（manifest 預設值）。
- 版面常數（spec §6.1）：家用白邊 `10 mm`；印刷廠出血 `3 mm`、安全區 `5 mm`；300 DPI；生成尺寸清單 `(1024,1024) (896,1152) (864,1216) (832,1216) (768,1344)`。
- 每次產生候選預設 `4` 張（範圍 1–8）；角色設定後不可更換。
- 文字上限：角色描述 1–800 字、場景描述 0–800 字（組合後的提示詞必須 < 2000，manifest 的 `prompt.max_length`）。
- 範圍外：帳號、QA、線稿 API、PDF、前端（Phase 3／4）。

## Review Focus

1. 後端在 generation 執行中重啟 → 啟動時該筆應回到 `queued` 並被重新執行，而不是永遠卡在 `running`。（Task 6 `test_running_jobs_requeued_on_start`）
2. ComfyUI 沒開或中途停止 → 任務留在 `queued`、系統狀態顯示「引擎無法連線」，ComfyUI 回來後自動繼續；不能把整排任務都標成失敗。（Task 6 `test_engine_unavailable_keeps_job_queued_and_retries`）
3. 選定一張「失敗／已捨棄／別頁／別本書」的 generation 當頁面或角色 → 回 409，不改資料。（Task 8 `test_select_rejects_invalid_generation`、Task 7 `test_set_character_rejects_invalid`）
4. 上傳的草圖不是圖片、太大（> 10 MB）或尺寸荒謬 → 400 並說明原因，不留下檔案。（Task 8 `test_sketch_upload_rejects_bad_files`）
5. 提示詞過長或含中文 → 角色描述、場景描述各限 800 字（組合後必定 < manifest 上限 2000），超過在建立時就回 422，不要排進佇列才失敗；中文照常保存。（Task 7 `test_candidates_validate_prompt_length`、Task 8 `test_scene_prompt_length`）

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/app/services/layout.py`（新） | 紙張尺寸、列印方式 → 內容範圍 mm／px、生成尺寸 |
| `backend/app/services/prompts.py`（新） | 角色與頁面提示詞組合（spec §10 文字） |
| `backend/app/models.py`（新） | SQLModel 資料表 Book、Page、Generation 與列舉 |
| `backend/app/db.py`（新） | engine 建立、建表、session 工廠 |
| `backend/app/storage.py`（新） | storage_dir 內的檔案路徑規則、寫檔、圖片驗證 |
| `backend/app/services/engine.py`（新） | 生成引擎介面：請求／結果資料類別、例外 |
| `backend/app/services/comfy_engine.py`（新） | ComfyUI 引擎：邏輯參數 → manifest 參數、上傳、執行、下載 |
| `comfy_workflows/sdxl_text2img/`（新） | 角色用純文生圖 workflow + manifest |
| `backend/app/services/worker.py`（新） | 單一背景 worker：取任務、執行、重試、進度 |
| `backend/app/api/books.py`（新） | 繪本、角色 API |
| `backend/app/api/pages.py`（新） | 頁面、草圖、頁面候選、選定 API |
| `backend/app/api/generations.py`（新） | generation 查詢、圖片、捨棄、取消、佇列狀態 |
| `backend/app/api/schemas.py`（新） | API 請求／回應模型 |
| `backend/app/config.py`（改） | 新增 `queue.retry_backoff_s` |
| `backend/app/main.py`（改） | 啟動 DB、worker、掛 router |
| `pyproject.toml`（改） | 加 `python-multipart` |

---

### Task 0: 起始 commit 與分支

repo 目前沒有任何 commit。**這一步需要使用者同意後才執行。**

- [ ] **Step 1:** `git add -A && git commit -m "chore: Phase 1 baseline (Route C experiments, lineart extraction, design doc)"`（`.gitignore` 已排除 `experiments/results/` 以外需要排除的項目；先 `git status` 確認沒有模型檔或 `storage/app.db`）
- [ ] **Step 2:** `git switch -c phase2-backend-core`

---

### Task 1: 版面計算

**Files:** Create `backend/app/services/layout.py`；Test `tests/test_layout.py`

**Interfaces:**
- Produces:
  - `class PaperSize(str, Enum)`: `A4, A5, B5, LETTER, SQUARE_21`（值：`"a4","a5","b5","letter","square_21"`）
  - `class PrintMode(str, Enum)`: `HOME="home"`, `PRINT_SHOP="print_shop"`
  - `PAPER_MM: dict[PaperSize, tuple[float, float]]` = A4 (210,297)、A5 (148,210)、B5 (182,257)、LETTER (215.9,279.4)、SQUARE_21 (210,210)
  - `GEN_SIZES: tuple[tuple[int,int], ...]` = spec 清單
  - `@dataclass(frozen=True) class Layout`: `paper_mm, content_mm, content_px, generation_size, safe_inset_mm: float | None`
  - `def compute_layout(size: PaperSize, mode: PrintMode, dpi: int = 300) -> Layout`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("size, mode, content_px, gen", [
    (PaperSize.A4, PrintMode.HOME, (2244, 3272), (832, 1216)),
    (PaperSize.A4, PrintMode.PRINT_SHOP, (2551, 3579), (864, 1216)),
    (PaperSize.A5, PrintMode.HOME, (1512, 2244), (832, 1216)),
    (PaperSize.B5, PrintMode.PRINT_SHOP, (2220, 3106), (864, 1216)),
    (PaperSize.LETTER, PrintMode.HOME, (2314, 3064), (896, 1152)),
    (PaperSize.LETTER, PrintMode.PRINT_SHOP, (2621, 3371), (896, 1152)),
    (PaperSize.SQUARE_21, PrintMode.HOME, (2244, 2244), (1024, 1024)),
])
def test_compute_layout(size, mode, content_px, gen):
    lay = compute_layout(size, mode)
    assert lay.content_px == content_px and lay.generation_size == gen

def test_home_has_margins_print_shop_has_bleed_and_safe_area():
    home = compute_layout(PaperSize.A4, PrintMode.HOME)
    shop = compute_layout(PaperSize.A4, PrintMode.PRINT_SHOP)
    assert home.content_mm == (190, 277) and home.safe_inset_mm is None
    assert shop.content_mm == (216, 303) and shop.safe_inset_mm == 8  # 出血 3 + 安全區 5
```

- [ ] **Step 2:** Run `... -m pytest tests/test_layout.py -q` → FAIL（module not found）
- [ ] **Step 3: Implement `compute_layout`.** 家用：content = paper − 2×10 mm；印刷廠：content = paper + 2×3 mm、`safe_inset_mm = 3 + 5`。px = round(mm / 25.4 × dpi)。生成尺寸：`min(GEN_SIZES, key=lambda wh: abs(log((wh[0]/wh[1]) / (cw/ch))))`。
- [ ] **Step 4:** Run tests → PASS
- [ ] **Step 5:** Commit `feat(layout): paper size and print mode layout`

---

### Task 2: 提示詞組合

**Files:** Create `backend/app/services/prompts.py`；Test `tests/test_prompts.py`

**Interfaces:**
- Produces:
  - `PAGE_STYLE = "children's picture book illustration, flat colors, thick clean black outlines, simple shapes, full detailed background scene"`
  - `PAGE_NEGATIVE = "plain background, empty background, white background, photo, realistic, 3d render, gradient, soft shading, airbrush, texture, noise, blurry, sketch, monochrome, grayscale, text, watermark, signature"`
  - `CHARACTER_NEGATIVE = "photo, realistic, 3d render, gradient, soft shading, airbrush, texture, noise, blurry, sketch, monochrome, grayscale, text, watermark, signature, multiple characters, cropped"`
  - `def page_prompt(character_description: str, scene: str) -> str` → `f"{character_description}, {scene}, {PAGE_STYLE}"`；scene 為空時省略該段（不留 `", ,"`）
  - `def character_prompt(description: str) -> str` → `f"character reference, single full body front view of {description}, standing, children's picture book illustration, flat colors, thick clean black outlines, simple shapes, plain white background, centered, whole body visible"`
  - 兩者都先 `strip()` 輸入。

- [ ] **Step 1: Write the failing tests**：`test_page_prompt_joins_parts`（`page_prompt(" a bunny ", "in a park") == "a bunny, in a park, " + PAGE_STYLE`）、`test_page_prompt_empty_scene`（`page_prompt("a bunny", "  ") == "a bunny, " + PAGE_STYLE`）、`test_character_prompt_contains_description_and_white_background`。
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit `feat(prompts): page and character prompt templates`

---

### Task 3: 資料模型、資料庫、檔案儲存

**Files:** Create `backend/app/models.py`、`backend/app/db.py`、`backend/app/storage.py`；Modify `pyproject.toml`（加 `"python-multipart>=0.0.9"` 到 dependencies，並 `pip install -e ".[dev]"`）；Test `tests/test_models_storage.py`

**Interfaces:**
- Produces（`app.models`）：
  - `class GenKind(str, Enum)`: `CHARACTER="character"`, `PAGE="page"`
  - `class GenStatus(str, Enum)`: `QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED`（小寫值）
  - `class PageStatus(str, Enum)`: `DRAFT="draft"`, `PASSED="passed"`（Phase 3 使用，先建欄位）
  - `class Book(SQLModel, table=True)`: `id: int | None (pk)`, `title: str`, `paper_size: PaperSize`, `print_mode: PrintMode`, `character_description: str | None`, `character_generation_id: int | None`, `created_at: datetime`, `updated_at: datetime`
  - `class Page(SQLModel, table=True)`: `id`, `book_id: int (fk book.id, index)`, `position: int`, `scene_prompt: str = ""`, `controlnet_strength: float = 0.4`, `sketch_path: str | None`, `selected_generation_id: int | None`, `status: PageStatus = DRAFT`, `created_at`, `updated_at`
  - `class Generation(SQLModel, table=True)`: `id`, `book_id (fk, index)`, `page_id: int | None (fk, index)`, `kind: GenKind`, `status: GenStatus = QUEUED (index)`, `discarded: bool = False`, `user_text: str`（使用者輸入的原文：角色描述或場景描述）, `prompt: str`, `negative_prompt: str`, `seed: int`, `width: int`, `height: int`, `request_json: str`（引擎請求的邏輯參數，JSON）, `engine: str | None`, `workflow_name/workflow_version/workflow_hash: str | None`, `models_json: str | None`, `params_json: str | None`（實際送出的全部 manifest 參數）, `image_path: str | None`, `elapsed_s: float | None`, `error: str | None`, `created_at`, `started_at: datetime | None`, `finished_at: datetime | None`
  - 時間一律 UTC（`datetime.now(timezone.utc)`）。
- Produces（`app.db`）：`def make_engine(database: Path) -> sqlalchemy.Engine`（`sqlite:///`，`check_same_thread=False`）、`def init_db(engine) -> None`（`SQLModel.metadata.create_all`）、`def session_factory(engine) -> Callable[[], Session]`
- Produces（`app.storage`）：
  - `class Storage`: `__init__(self, root: Path)`；`generation_image(book_id, gen_id) -> str`（相對路徑 `books/{book_id}/generations/{gen_id}.png`）；`sketch(book_id, page_id) -> str`（`books/{book_id}/pages/{page_id}/sketch.png`）；`abs(rel: str) -> Path`；`write(rel: str, data: bytes) -> Path`（建父資料夾、先寫暫存檔再 rename）；`delete(rel: str) -> None`（不存在不報錯）
  - `class ImageRejected(ValueError)`
  - `def normalize_png(data: bytes, *, max_bytes: int = 10_000_000, max_side: int = 8192) -> tuple[bytes, tuple[int, int]]`：用 Pillow 開啟（PNG/JPEG/WebP），驗證大小與邊長（最短邊 ≥ 64），轉成 RGB PNG bytes；失敗丟 `ImageRejected`（訊息說明原因）。

- [ ] **Step 1: Write the failing tests**：`test_tables_roundtrip`（建 Book → Page → Generation，讀回 enum 與預設值：`page.controlnet_strength == 0.4`、`gen.status == GenStatus.QUEUED`、`page.status == PageStatus.DRAFT`）；`test_storage_paths_and_write`（`generation_image(3, 7) == "books/3/generations/7.png"`、`write` 後 `abs()` 檔案存在）；`test_normalize_png_rejects`（非圖片 bytes、`max_bytes` 超過、32×32 圖 → 都丟 `ImageRejected`；JPEG 輸入 → 回 PNG 開頭 `b"\x89PNG"`）。
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement 三個檔案、加依賴並安裝
- [ ] **Step 4:** Run → PASS（全套 `pytest -q` 也要通過）
- [ ] **Step 5:** Commit `feat(db): books, pages, generations models and storage`

---

### Task 4: 生成引擎介面與 ComfyUI 引擎、角色 workflow

**Files:** Create `backend/app/services/engine.py`、`backend/app/services/comfy_engine.py`、`comfy_workflows/sdxl_text2img/workflow_api.json`、`comfy_workflows/sdxl_text2img/manifest.yaml`；Test `tests/test_comfy_engine.py`

**Interfaces:**
- Consumes: `ComfyClient`、`WorkflowRegistry`／`WorkflowEntry.build_prompt`、`ComfyConnectionError`、`ComfyError`、`ParameterError`（既有）
- Produces（`app.services.engine`）：
  - `@dataclass class GenerationRequest`: `kind: GenKind`, `prompt: str`, `negative_prompt: str`, `seed: int`, `width: int`, `height: int`, `character_image: Path | None = None`, `structure_image: Path | None = None`, `structure_strength: float = 0.0`
  - `@dataclass class GenerationResult`: `image: bytes`, `elapsed_s: float`, `engine: str`, `workflow_name: str`, `workflow_version: str`, `workflow_hash: str`, `models: list[str]`, `params: dict`
  - `class EngineError(Exception)`、`class EngineUnavailable(EngineError)`（引擎連不上，可重試）
  - `ProgressFn = Callable[[int, int], None]`
  - `class GenerationEngine(Protocol)`: `async def generate(self, req: GenerationRequest, on_progress: ProgressFn | None = None) -> GenerationResult`
- Produces（`app.services.comfy_engine`）：
  - `CHARACTER_WORKFLOW = "sdxl_text2img"`、`PAGE_WORKFLOW = "route_a_sdxl_ipadapter_cn"`
  - `PAGE_FIXED = {"ipadapter_weight": 0.55, "ipadapter_end_at": 0.6, "controlnet_type": "hed/pidi/scribble/ted", "controlnet_end_percent": 0.5}`
  - `class ComfyEngine`: `__init__(self, client_factory: Callable[[], ComfyClient], registry: WorkflowRegistry, run_timeout_s: float)`；實作 `generate`
  - `def page_params(req: GenerationRequest, character_ref: str, structure_ref: str) -> dict`（純函式，方便測試）：回傳 `{**PAGE_FIXED, prompt, negative_prompt, seed, width, height, character_image: character_ref, structure_image: structure_ref, controlnet_strength: req.structure_strength if req.structure_image else 0.0}`
- 行為：
  - 頁面請求必須有 `character_image`，否則 `EngineError("頁面生成需要角色參考圖")`。
  - 沒有 `structure_image` 時，產生一張 `width×height` 全白 PNG 上傳當 structure_image（強度 0）。
  - 圖片上傳以內容 sha256 前 12 碼命名、子資料夾 `coloring_books`，同一 `ComfyEngine` 實例內快取。
  - `ComfyConnectionError` → `EngineUnavailable`；其他 `ComfyError`、`ParameterError`、`WorkflowNotReady`、`KeyError`（workflow 不存在）→ `EngineError`（訊息保留原因）。
  - 進度：把 ComfyClient 的 `progress` 事件轉成 `on_progress(value, max)`。
  - 結果取 manifest `outputs` 對應節點的第一張 `type == "output"` 圖。
- `sdxl_text2img` workflow：節點 CheckpointLoaderSimple(`CKPT`)、CLIPTextEncode(`POSITIVE`、`NEGATIVE`)、EmptyLatentImage(`LATENT`)、KSampler(`SAMPLER`)、VAEDecode(`DECODE`)、SaveImage(`OUTPUT`，`filename_prefix: coloring_books/character`)。manifest 參數：`prompt`(string, required, max_length 2000)、`negative_prompt`(string, max_length 2000)、`width`/`height`(int 512–1536, 預設 832/1216)、`seed`(int 0–4294967295, required)、`steps`(10–60, 30)、`cfg`(1–12, 6.0)、`sampler_name`(enum 同 route_a, dpmpp_2m)、`scheduler`(enum 同 route_a, karras)；models：同 route_a 的 `sdxl_base` 條目。

- [ ] **Step 1: Write the failing tests**（用既有 `FakeComfy`，registry 指向 repo 的 `comfy_workflows/`）
  - `test_page_params_uses_spec_defaults`：`page_params(req(structure=None, strength=0.4), "c.png", "w.png")["controlnet_strength"] == 0.0`；有 structure 時 `== 0.4`；`ipadapter_weight == 0.55` 等 `PAGE_FIXED` 值。
  - `test_generate_page_sends_whitelisted_inputs`：送出的 prompt 中 title `IPADAPTER` 節點 `weight == 0.55`、`end_at == 0.6`；`UNION_TYPE.type == "hed/pidi/scribble/ted"`；`LATENT.width/height == req` 值；角色圖只上傳一次（同一張跑兩次 `len(fake.uploads)` 為 2：角色＋白圖，第二次不增加）。
  - `test_generate_character_uses_text2img`：`result.workflow_name == "sdxl_text2img"`、`result.models == ["sd_xl_base_1.0.safetensors"]`、`result.image.startswith(b"\x89PNG")`。
  - `test_engine_errors_mapped`：`fake.dead = True` → `EngineUnavailable`；`fake.mode = "error"` → `EngineError` 且不是 `EngineUnavailable`，訊息含 `"CUDA out of memory"`；page 請求缺角色圖 → `EngineError`。
  - `test_text2img_manifest_loads`：`WorkflowRegistry(repo/comfy_workflows).load().get("sdxl_text2img").ok`。
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** 寫 workflow JSON 與 manifest、實作兩個模組
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5（手動，需要 GPU）：** 用臨時 ComfyUI（`127.0.0.1:8190`）驗證新 workflow：`APP_COMFY__URL=http://127.0.0.1:8190 ... scripts/preflight.py` 顯示 `sdxl_text2img` 全部通過；再以 `ComfyEngine` 實際產一張角色圖確認可開啟。結果記在 commit 訊息。
- [ ] **Step 6:** Commit `feat(engine): generation engine interface, ComfyUI engine, text2img workflow`

---

### Task 5: 設定與 worker 依賴

**Files:** Modify `backend/app/config.py`、`config/settings.example.yaml`；Test `tests/test_config.py`

**Interfaces:**
- Produces: `class QueueSettings(BaseModel)`: `retry_backoff_s: float = Field(10.0, ge=0)`；`Settings.queue: QueueSettings = QueueSettings()`（沒寫也能載入）。範本加上 `queue:` 段落與註解。

- [ ] **Step 1:** 測試 `test_queue_defaults_and_env_override`：舊設定檔載入 → `retry_backoff_s == 10.0`；`APP_QUEUE__RETRY_BACKOFF_S=0` → `0.0`。
- [ ] **Step 2:** Run → FAIL　**Step 3:** Implement　**Step 4:** Run → PASS
- [ ] **Step 5:** Commit `feat(config): queue retry backoff setting`

---

### Task 6: 單一背景 worker

**Files:** Create `backend/app/services/worker.py`；Test `tests/test_worker.py`

**Interfaces:**
- Consumes: `GenerationEngine`、`GenerationRequest`、`EngineError`、`EngineUnavailable`（Task 4）；`Generation`、`GenStatus`（Task 3）；`Storage`（Task 3）
- Produces:
  - `class WorkerState(str, Enum)`: `IDLE, RUNNING, ENGINE_UNAVAILABLE, STOPPED`
  - `class GenerationWorker`:
    - `__init__(self, engine: GenerationEngine, sessions: Callable[[], Session], storage: Storage, retry_backoff_s: float)`
    - `async def start(self) -> None`：先 `requeue_interrupted()`，再建立背景 task
    - `async def stop(self) -> None`
    - `def notify(self) -> None`：有新任務時喚醒（`asyncio.Event`）
    - `def requeue_interrupted(self) -> int`：把 `RUNNING` 改回 `QUEUED`、清 `started_at`，回傳筆數
    - `async def run_once(self) -> bool`：取 `id` 最小的 `QUEUED` 執行一筆；沒有任務回 False（測試直接呼叫，不靠背景 task）
    - `state: WorkerState`、`last_error: str | None`、`progress: dict[int, tuple[int, int]]`（generation id → (value, max)，完成後移除）
    - `def queue_position(self, gen_id: int) -> int | None`：0＝執行中，1＝下一個…；非排隊中回 None
- 行為：
  - 取任務 → `RUNNING`、`started_at`；由 `request_json` 還原 `GenerationRequest`（圖片欄位存的是 storage 相對路徑，轉成絕對路徑）。
  - 成功 → 圖寫到 `storage.generation_image(book_id, id)`、填入 `GenerationResult` 全部紀錄欄位、`SUCCEEDED`、`finished_at`。
  - `EngineUnavailable` → 回到 `QUEUED`（`started_at=None`）、`state=ENGINE_UNAVAILABLE`、`last_error`、等 `retry_backoff_s` 後再試（`notify()` 可提前喚醒）。
  - 其他 `EngineError` 或非預期例外 → `FAILED`、`error`、`finished_at`；worker 繼續下一筆，不會結束。
  - 執行中被設成 `CANCELLED` 的不處理（取消只允許 queued，見 Task 9）。

- [ ] **Step 1: Write the failing tests**（`FakeEngine`：可設定回傳 PNG、丟 `EngineError`、丟 `EngineUnavailable`，並記錄收到的請求）
  - `test_run_once_success_records_everything`：成功後 `status == SUCCEEDED`、`image_path == "books/1/generations/1.png"` 且檔案存在、`workflow_name/version/hash/models_json/params_json/elapsed_s` 都有值、`started_at <= finished_at`。
  - `test_failure_marks_failed_and_continues`：第一筆丟 `EngineError("boom")` → `FAILED` 且 `error` 含 `"boom"`；第二筆照常成功。
  - `test_engine_unavailable_keeps_job_queued_and_retries`：丟 `EngineUnavailable` → 仍 `QUEUED`、`worker.state == ENGINE_UNAVAILABLE`；引擎恢復後 `run_once()` → `SUCCEEDED`、`state` 不再是 `ENGINE_UNAVAILABLE`。
  - `test_running_jobs_requeued_on_start`：預先存一筆 `RUNNING` → `requeue_interrupted() == 1` 且變 `QUEUED`。
  - `test_fifo_and_queue_position`：三筆 queued → `queue_position` 依序 0／1／2（最小 id 為 0）；`run_once` 依 id 順序處理。
  - `test_background_loop_processes_after_notify`：`start()` 後新增一筆並 `notify()`，`await asyncio.wait_for(...)` 直到 `SUCCEEDED`（逾時 2 s），最後 `stop()`。
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement（資料庫操作用同步 `Session`，每次操作開新 session；引擎呼叫在 async 中）
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit `feat(worker): single background generation worker with retry`

---

### Task 7: 繪本與角色 API

**Files:** Create `backend/app/api/__init__.py`、`backend/app/api/schemas.py`、`backend/app/api/books.py`、`backend/app/api/deps.py`；Modify `backend/app/main.py`；Test `tests/test_api_books.py`

**Interfaces:**
- Consumes: `compute_layout`（Task 1）、`character_prompt`／`CHARACTER_NEGATIVE`（Task 2）、models／Storage（Task 3）、`GenerationWorker.notify`（Task 6）
- Produces:
  - `app.api.deps`：`get_session`、`get_storage`、`get_worker`（從 `request.app.state` 取）
  - `create_app(settings: Settings | None = None, *, engine: GenerationEngine | None = None) -> FastAPI`：lifespan 內建 DB（`make_engine(settings.paths.database)` + `init_db`）、`Storage(settings.paths.storage_dir)`、`ComfyEngine`（未傳入 engine 時）、`GenerationWorker` 並 `start()`；結束時 `stop()`。preflight 行為不變。
  - schemas：`BookCreate{title: str (1–200), paper_size: PaperSize, print_mode: PrintMode}`、`BookOut{id, title, paper_size, print_mode, character_description, character_generation_id, layout: {content_mm, content_px, generation_size, safe_inset_mm}, created_at, updated_at}`、`BookUpdate{title}`、`CharacterCandidatesCreate{description: str (1–800), count: int = 4 (1–8), seed: int | None}`、`GenerationOut{id, book_id, page_id, kind, status, discarded, prompt, seed, width, height, image_url: str | None, error, elapsed_s, queue_position, progress: [value, max] | None, created_at}`
  - 路由：
    - `POST /api/books` → 201 `BookOut`；`GET /api/books`（新到舊）；`GET /api/books/{id}`；`PATCH /api/books/{id}`（只改 title）；不存在 → 404
    - `POST /api/books/{id}/character/candidates`（body `{description, count, seed?}`）→ 202，回 `list[GenerationOut]`：建立 `count` 筆 `kind=CHARACTER` 的 queued generation，seed 為 `seed` 或隨機基底，依序 +0..count-1；尺寸固定 832×1216；`user_text = description`、`prompt = character_prompt(description)`、`negative_prompt = CHARACTER_NEGATIVE`；並 `worker.notify()`。角色已設定 → 409。
    - `POST /api/books/{id}/character`（body `{generation_id}`）→ 設定 `character_generation_id` 與 `character_description = generation.user_text`；已設定 → 409；generation 不屬於本書、不是 CHARACTER、不是 SUCCEEDED、或已 discarded → 409。
  - `image_url` = `/api/generations/{id}/image`（有圖時）。

- [ ] **Step 1: Write the failing tests**（`TestClient(create_app(load_settings(settings_file), engine=FakeEngine()))`；需要 worker 完成時直接 `await app.state.worker.run_once()` 或用 `anyio` 跑）
  - `test_create_and_get_book_with_layout`：A4＋print_shop → `layout.generation_size == [864, 1216]`、`content_px == [2551, 3579]`；不合法 `paper_size` → 422。
  - `test_character_candidates_queue_four`：回 4 筆、`status == "queued"`、seed 連號、`kind == "character"`、`width == 832`；中文描述原樣保存在 `prompt` 內。
  - `test_candidates_validate_prompt_length`：description 801 字 → 422，資料庫沒有新 generation；800 字中文 → 202 且 `prompt` 長度 < 2000。
  - `test_set_character_and_lock`：worker 跑完一筆 → 設定成功、`character_description` 正確；再設定或再產生候選 → 409。
  - `test_set_character_rejects_invalid`：FAILED、discarded、別本書的 generation、PAGE 類型 → 都 409，book 未改變。
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement
- [ ] **Step 4:** Run → PASS；既有 `tests/test_main.py` 不修改也要通過（未傳 engine 時建立 ComfyEngine，但沒有任務，worker 不會呼叫它）
- [ ] **Step 5:** Commit `feat(api): books and character endpoints`

---

### Task 8: 頁面、草圖、頁面候選、選定 API

**Files:** Create `backend/app/api/pages.py`；Modify `backend/app/api/schemas.py`、`backend/app/main.py`；Test `tests/test_api_pages.py`

**Interfaces:**
- Consumes: Task 1–3、Task 7 的 schemas／deps、`page_prompt`／`PAGE_NEGATIVE`
- Produces:
  - schemas：`PageCreate{scene_prompt: str = "" (≤ 800)}`、`PageUpdate{scene_prompt?: str (≤ 800), controlnet_strength?: float (0.0–0.6)}`、`PageCandidatesCreate{count: int = 4 (1–8), seed: int | None}`、`PageOrder{page_ids: list[int]}`、`SelectGeneration{generation_id: int}`、`PageOut{id, book_id, position, scene_prompt, controlnet_strength, sketch_url: str | None, selected_generation_id, status, created_at, updated_at}`
  - 路由：
    - `POST /api/books/{id}/pages` → 201，`position` = 目前最大 +1；`GET /api/books/{id}/pages`（依 position）
    - `PATCH /api/pages/{id}`
    - `POST /api/books/{id}/pages/order`：`page_ids` 必須剛好是本書全部頁面（否則 400），依序設 position 0..n-1
    - `PUT /api/pages/{id}/sketch`（multipart 欄位 `file`）→ `normalize_png` 後存 `storage.sketch(...)`；`ImageRejected` → 400 並帶原因，不留檔；`DELETE /api/pages/{id}/sketch` → 刪檔、`sketch_path=None`；`GET /api/pages/{id}/sketch` → 圖片
    - `POST /api/pages/{id}/candidates`（body `{count=4, seed?}`）→ 202 `list[GenerationOut]`：本書未設定角色 → 409「請先設定角色」；建立 `count` 筆 PAGE generation：`user_text = page.scene_prompt`、`prompt = page_prompt(book.character_description, page.scene_prompt)`、`negative = PAGE_NEGATIVE`、尺寸＝`compute_layout(book).generation_size`、`request_json` 存角色圖（角色 generation 的 `image_path`）、草圖相對路徑（**複製**一份到 `books/{book}/pages/{page}/sketch_{gen_id}.png`，之後改草圖不影響已排隊或歷史紀錄）、`structure_strength = page.controlnet_strength`；`worker.notify()`
    - `POST /api/pages/{id}/select`（body `{generation_id}`）→ 必須是本頁、PAGE、SUCCEEDED、未 discarded，否則 409；設 `selected_generation_id`
- [ ] **Step 1: Write the failing tests**
  - `test_pages_append_and_reorder`：建 3 頁 position 0/1/2；`order` 反轉後 GET 順序反轉；`order` 少一頁 → 400。
  - `test_controlnet_strength_range`：0.7 → 422；0.6 → 200。
  - `test_scene_prompt_length`：801 字 → 422；800 字＋800 字角色描述產生候選 → `prompt` 長度 < 2000。
  - `test_sketch_upload_rejects_bad_files`：文字檔 → 400 且 `detail` 說明不是圖片；11 MB → 400；32×32 → 400；三者後 storage 下沒有 sketch 檔、`sketch_path is None`；正常 PNG → 200 且 `sketch_url` 有值。
  - `test_page_candidates_require_character`：未設角色 → 409。
  - `test_page_candidates_snapshot_sketch`：設好角色與草圖後產生候選 → `request_json.structure_image` 指向 `sketch_{gen_id}.png`（檔案存在）、`structure_strength == 0.4`、`width/height == layout.generation_size`；之後刪草圖，已建立的 snapshot 檔仍在。
  - `test_select_rejects_invalid_generation`：FAILED、discarded、別頁、CHARACTER 類型 → 409；成功的 → 200 且 `selected_generation_id` 更新；重新產生候選不改 `selected_generation_id`。
- [ ] **Step 2:** Run → FAIL　**Step 3:** Implement　**Step 4:** Run → PASS
- [ ] **Step 5:** Commit `feat(api): pages, sketches, page candidates and selection`

---

### Task 9: generation 查詢、圖片、捨棄、取消、佇列狀態

**Files:** Create `backend/app/api/generations.py`；Modify `backend/app/main.py`；Test `tests/test_api_generations.py`

**Interfaces:**
- Consumes: Task 3、6、7
- Produces:
  - `GET /api/generations/{id}` → `GenerationOut`（含 `queue_position`、`progress`）
  - `GET /api/generations/{id}/image` → PNG（未完成 → 404）
  - `GET /api/books/{id}/generations?kind=&page_id=&include_discarded=false` → 新到舊
  - `PATCH /api/generations/{id}`（body `{discarded: bool}`）→ 已被頁面選定或為本書角色的 generation 不能捨棄（409）
  - `POST /api/generations/{id}/cancel` → 只有 QUEUED 可取消（→ CANCELLED），其他 409
  - `GET /api/system/queue` → `{state: WorkerState, last_error, queued: int, running_id: int | None}`
- [ ] **Step 1: Write the failing tests**
  - `test_generation_detail_progress_and_image`：queued 時 `queue_position == 0`、`image_url is None`；跑完後 `image_url` 可下載且為 PNG。
  - `test_discard_and_list_filter`：捨棄後預設清單不含、`include_discarded=true` 才有；已選定的 → 409。
  - `test_cancel_only_queued`：queued → 200 且 worker `run_once()` 回 False（沒有可做的）；succeeded → 409。
  - `test_queue_status_reports_engine_unavailable`：FakeEngine 丟 `EngineUnavailable` 後 `GET /api/system/queue` → `state == "engine_unavailable"`、`last_error` 有值。
- [ ] **Step 2:** Run → FAIL　**Step 3:** Implement　**Step 4:** Run → PASS
- [ ] **Step 5:** Commit `feat(api): generation history, discard, cancel, queue status`

---

### Task 10: 端對端手動驗證與 Phase 2 結束報告

**Files:** Modify `README.md`（API 一覽、啟動方式）

- [ ] **Step 1:** 全套測試：`env -u PYTHONPATH $PY -m pytest -q` → 全部通過
- [ ] **Step 2（手動，需要 GPU）：** 啟動臨時 ComfyUI（8190）與後端（`APP_COMFY__URL=http://127.0.0.1:8190 uvicorn app.main:app --app-dir backend --port 8000`），用 curl 走一次：建 A4／家用繪本 → 角色候選 4 張 → 設定角色 → 建頁、上傳 `experiments/samples/pose/park_swing_props.png` → 頁面候選 4 張 → 選定一張；確認 `GET /api/generations/{id}` 的紀錄欄位完整、圖片可下載、`GET /api/system/queue` 狀態正確；中途停掉 ComfyUI 確認任務留在 queued、重開後繼續。
- [ ] **Step 3:** README 加上 API 一覽與啟動指令
- [ ] **Step 4:** Commit `docs: Phase 2 API overview`
- [ ] **Step 5:** 依 CLAUDE.md 停下來回報：完成項目、測試方式、需要使用者手動做的事。
