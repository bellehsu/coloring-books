# Phase 3 線稿、QA、匯出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓後端可以把頁面選定的彩圖即時轉成可列印線稿（細緻度、線寬可調，手動修線圖層不會因調參數消失），自動檢查線條閉合並標出缺口，管理頁面「草稿／通過」狀態，並依繪本尺寸與列印方式輸出整本 PDF。

**Architecture:** 線稿與 QA 都是確定性的影像處理，在 API 的 threadpool 同步執行（A4 一頁約 0.3 秒），不進 GPU worker。頁面線稿以「輸入雜湊」快取在 storage，輸入（選定圖、細緻度、線寬、修改圖層、內容尺寸）改變才重算。資料庫改用 Alembic 管理結構變更。PDF 用 Pillow 產生（不加新依賴）。

**Tech Stack:** Python 3.12、FastAPI、SQLModel、SQLite、Alembic（新增）、OpenCV、scikit-image、Pillow、pytest。

**Spec:** `docs/superpowers/specs/2026-11-06-coloring-book-web-design.md`（本計畫實作 §11 的 Phase 3：§6.1 換尺寸規則與參考線、§6.3 線條參數與上傳參考圖、§6.5 匯出、§7 QA、§8 線稿與手動修線；另含使用者 2026-11-06 決定：導入 Alembic、封存 Route B、調整角色提示詞）

## Global Constraints

- 遵守 `CLAUDE.md` 與 Phase 2 既有慣例：單一 worker、manifest 白名單、page 只存 `selected_generation_id`、時間用 `app.models.utcnow()`、資料庫存 storage 相對路徑、`Storage.write` 原子寫入、條件式 UPDATE + rowcount 防競爭。
- 測試：`env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`，不需 GPU／ComfyUI。
- 線稿參數（spec §8）：細緻度 `simple`＝xdog sigma `1.5`、`standard`＝`1.2`（預設）、`detailed`＝`0.8`；其餘 xdog 參數用 `METHODS["xdog"]` 預設。線寬 `8–16` px（預設 `11`），單位一律是 300 DPI 像素，所以換紙張尺寸時紙上粗細不變。
- 線稿流程：選定圖（生成解析度）→ `lineart_extract.extract(xdog)` → `lineart_post.process`（`target`＝繪本 `content_px`、`fit="contain"`、`line_width_px`）→ 疊上修改圖層 → QA → 1-bit PNG（300 DPI）。
- 修改圖層座標為內容範圍的相對座標 0–1；筆寬單位為 300 DPI px。
- 版面（spec §6.1）：家用 PDF 頁＝紙張大小，內容置中（白邊 10 mm）；印刷廠 PDF 頁＝內容範圍（紙張＋3 mm 出血），裁切框距邊 `round(3 mm)`＝35 px，安全區再內縮 `round(5 mm)`＝59 px。第一版不做裁切標記、封面、頁碼。
- 頁面狀態只有 `draft`／`passed`。通過條件：已選圖、角色外型標為「一致」、自動 QA 無錯誤（有錯誤可強制通過，需填原因；角色「不一致」不可強制通過）。
- 匯出一律用繪本目前的尺寸；預設只含 `passed` 頁面。
- 不新增模型、不改 ComfyUI workflow。

## Review Focus

1. 已通過的頁面在繪本改尺寸或列印方式後 → 線稿依新內容尺寸重算、線寬 px 不變、修改圖層跟著換算；新 QA 有錯的頁面退回草稿，沒錯的維持通過。（Task 10 `test_change_size_recomputes_and_demotes`）
2. 修改圖層送出時，選定圖已在別的分頁被換掉 → 409，不能把筆畫存到錯的圖上。（Task 8 `test_edits_reject_stale_generation`）
3. 修改圖層內容不合法或過大（座標超出 0–1、NaN、超過點數上限）→ 422，不寫入任何東西。（Task 8 `test_edits_validation`）
4. 匯出時沒有通過的頁面、頁面沒選圖、選定圖檔遺失 → 清楚的 409 訊息，不是 500。（Task 7 `test_missing_image_raises`、Task 11 `test_export_errors`）
5. Phase 2 留下的資料庫（沒有 `alembic_version` 表）→ 啟動時自動標記為基準版本並升級，既有資料保留。（Task 2 `test_legacy_db_is_stamped_and_upgraded`）

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `archive/comfy_workflows/route_b_qwen_edit_2511_gguf/`（移入） | 封存不用的 Route B manifest |
| `backend/alembic.ini`、`backend/migrations/`（新） | Alembic 設定、env、版本腳本 |
| `backend/app/db.py`（改） | `init_db` 改為執行 migration（含舊庫自動標記） |
| `backend/app/services/layout.py`（改） | PDF 頁尺寸、內容位移、裁切框、安全區、比例群組 |
| `backend/app/services/lineart_render.py`（新） | 細緻度、彩圖 → 線稿、套用修改圖層 |
| `backend/app/services/qa.py`（新） | 線頭、缺口偵測、QA 報告與門檻 |
| `backend/app/services/page_lineart.py`（新） | 頁面線稿快取、重算、QA 存檔、狀態退回 |
| `backend/app/services/export.py`（新） | 組版面頁、PDF |
| `backend/app/api/lineart.py`（新） | 線稿、修改圖層、QA、overlay、預覽、參考圖轉草圖 |
| `backend/app/api/review.py`（新） | 角色檢查、通過／退回 |
| `backend/app/api/export.py`（新） | 匯出摘要、PDF、單頁 PNG |
| `backend/app/models.py`、`api/schemas.py`、`api/pages.py`、`api/books.py`、`storage.py`、`main.py`（改） | 新欄位、輸出欄位、狀態規則、掛 router |

---

### Task 1: 封存 Route B

**Files:** Move `comfy_workflows/route_b_qwen_edit_2511_gguf/` → `archive/comfy_workflows/route_b_qwen_edit_2511_gguf/`；Modify `CLAUDE.md:29`、`tests/test_workflow_registry.py:144-150`、`experiments/configs/phase1_example.yaml`（如果有測試載入它）

- [ ] **Step 1:** `git mv comfy_workflows/route_b_qwen_edit_2511_gguf archive/comfy_workflows/route_b_qwen_edit_2511_gguf`；在 `archive/README.md` 寫一行：「已淘汰、不會被 preflight 掃描的 workflow；要恢復就搬回 comfy_workflows/。」
- [ ] **Step 2:** 把 `test_repo_route_b_manifest_valid_but_waiting_for_workflow` 改成 `test_repo_registry_has_no_retired_routes`：載入 repo 的 `comfy_workflows`，斷言 `route_b_qwen_edit_2511_gguf` 不在登錄中、`sdxl_text2img` 與 `route_a_sdxl_ipadapter_cn` 在。
- [ ] **Step 3:** `grep -rn route_b experiments tests backend scripts`；仍引用它的設定或測試改成指向 archive 路徑或移除該項（範例設定檔可刪除那個 route 區塊）。
- [ ] **Step 4:** CLAUDE.md 第 29 行改為：「…`route_b_qwen_edit_2511_gguf` 已移到 `archive/comfy_workflows/`（preflight 不掃描）。」
- [ ] **Step 5:** 跑全部測試 → PASS；commit `chore: archive retired Route B workflow`

---

### Task 2: 導入 Alembic

**Files:** Modify `pyproject.toml`、`backend/app/db.py`；Create `backend/alembic.ini`、`backend/migrations/env.py`、`backend/migrations/script.py.mako`、`backend/migrations/versions/0001_phase2_baseline.py`；Test `tests/test_migrations.py`

**Interfaces:**
- Produces: `init_db(engine: Engine) -> None`（簽名不變；改為 migration）、`BASELINE_REVISION = "0001"`、`def alembic_config(engine: Engine) -> alembic.config.Config`
- 之後每個改資料表的 Task 都要新增一個 `versions/000N_*.py`；`test_schema_matches_models` 會抓漏。

- [ ] **Step 1:** `pyproject.toml` 加 `"alembic>=1.13"`；用 venv 的 pip 安裝（只有 PyPI 套件，不是模型）。
- [ ] **Step 2: Write the failing tests**（`tests/test_migrations.py`）

```python
def test_fresh_db_upgrades_to_head(tmp_path):
    eng = make_engine(tmp_path / "a.db"); init_db(eng)
    assert {"book", "page", "generation", "alembic_version"} <= set(inspect(eng).get_table_names())

def test_schema_matches_models(tmp_path):
    eng = make_engine(tmp_path / "a.db"); init_db(eng)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"render_as_batch": True}), SQLModel.metadata)
    assert diff == []

def test_legacy_db_is_stamped_and_upgraded(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    command.upgrade(alembic_config(eng), BASELINE_REVISION)
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE alembic_version")
        c.exec_driver_sql("INSERT INTO book (title, paper_size, print_mode, created_at, updated_at) "
                          "VALUES ('舊書', 'A4', 'HOME', '2026-11-06 00:00:00', '2026-11-06 00:00:00')")
    init_db(eng)
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT title FROM book").scalar() == "舊書"
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == head_revision()
    init_db(eng)  # 再跑一次不出錯
```

（enum 欄位在 SQLite 存的是成員名稱，例如 `'A4'`、`'HOME'`；實作時先用 Phase 2 的 `create_all` 產生的庫確認，測試的 INSERT 值以實際為準。）

- [ ] **Step 3: 實作**
  - `alembic.ini` 只放 `script_location = %(here)s/migrations`，不寫資料庫網址（CLAUDE.md：路徑只來自 settings）。`env.py`：若 `config.attributes["connection"]` 存在就用它；否則 `load_settings().paths.database` 組網址（給 CLI 用）。`target_metadata = SQLModel.metadata`（`import app.models`），`render_as_batch=True`。`script.py.mako` 要 `import sqlmodel`（AutoString）。
  - `0001_phase2_baseline.py`：用 `alembic revision --autogenerate` 對空資料庫產生，內容等於 Phase 2 的三張表；手動確認後 commit。
  - `init_db`：在一個 connection 上，如果有 `book` 表但沒有 `alembic_version` → `command.stamp(cfg, BASELINE_REVISION)`；然後 `command.upgrade(cfg, "head")`。`cfg.attributes["connection"] = conn`。
- [ ] **Step 4:** 全部測試 PASS（既有 API 測試透過 `create_app` 走新的 `init_db`）。
- [ ] **Step 5:** README 加一節「資料庫結構變更」：改 models 後執行 `cd backend && alembic revision --autogenerate -m "..."`，檢查產生的檔案，啟動時自動升級。commit `feat(db): manage schema with Alembic`

---

### Task 3: 版面參考線與比例群組

**Files:** Modify `backend/app/services/layout.py`、`backend/app/api/schemas.py`（`LayoutOut`）；Test `tests/test_layout.py`

**Interfaces:**
- Produces（加到 `Layout`）：`page_px: tuple[int,int]`（PDF 一頁的像素）、`content_offset_px: tuple[int,int]`（內容貼在頁上的位置）、`trim_box_px: tuple[int,int,int,int] | None`、`safe_box_px: tuple[int,int,int,int] | None`（兩者都是內容座標系的 x0,y0,x1,y1）；`def ratio_group(size: PaperSize) -> str`（`"iso"`：A4/A5/B5；`"letter"`；`"square"`）。
- `LayoutOut` 加同名四個欄位與 `paper_mm`、`ratio_group`。

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("size, mode, page_px, offset, trim, safe", [
    (PaperSize.A4, PrintMode.HOME, (2480, 3508), (118, 118), None, None),
    (PaperSize.A4, PrintMode.PRINT_SHOP, (2551, 3579), (0, 0), (35, 35, 2515, 3543), (94, 94, 2456, 3484)),
    (PaperSize.B5, PrintMode.HOME, (2150, 3035), (118, 118), None, None),
    (PaperSize.LETTER, PrintMode.PRINT_SHOP, (2621, 3371), (0, 0), (35, 35, 2585, 3335), (94, 94, 2526, 3276)),
    (PaperSize.SQUARE_21, PrintMode.HOME, (2480, 2480), (118, 118), None, None),
])
def test_page_geometry(size, mode, page_px, offset, trim, safe): ...

def test_ratio_groups():
    assert {ratio_group(s) for s in (PaperSize.A4, PaperSize.A5, PaperSize.B5)} == {"iso"}
    assert ratio_group(PaperSize.LETTER) == "letter" and ratio_group(PaperSize.SQUARE_21) == "square"
```

- [ ] **Step 2: 實作** 紙張像素 `round(mm / 25.4 * dpi)`。家用：`page_px`＝紙張像素、位移＝`((page_w - content_w) // 2, (page_h - content_h) // 2)`。印刷廠：`page_px`＝`content_px`、位移 `(0,0)`、裁切框＝`(b, b, b + paper_w, b + paper_h)`（`b = round(3 mm)`）、安全區＝裁切框各邊內縮 `round(5 mm)`。
- [ ] **Step 3:** 測試 PASS（含 `test_api_books` 的 layout 斷言，必要時更新）；commit `feat(layout): page geometry, trim/safe boxes, ratio groups`

---

### Task 4: 線稿渲染與修改圖層

**Files:** Create `backend/app/services/lineart_render.py`；Test `tests/test_lineart_render.py`

**Interfaces:**
- Produces:
  - `class LineDetail(str, Enum)`: `SIMPLE="simple"`, `STANDARD="standard"`, `DETAILED="detailed"`；`DETAIL_SIGMA = {SIMPLE: 1.5, STANDARD: 1.2, DETAILED: 0.8}`
  - `LINE_WIDTH_MIN, LINE_WIDTH_MAX, LINE_WIDTH_DEFAULT = 8, 16, 11`；`RENDER_VERSION = 1`（演算法改變時 +1，讓快取失效）
  - `@dataclass(frozen=True) class Stroke`: `op: Literal["draw","erase"]`、`width_px: float`、`points: tuple[tuple[float,float], ...]`（0–1 相對座標）
  - `def render_base(color: np.ndarray, content_px: tuple[int,int], detail: LineDetail, line_width_px: int) -> tuple[np.ndarray, dict]`：回傳 (bool ink, `lineart_post` 指標)
  - `def apply_strokes(ink: np.ndarray, strokes: Sequence[Stroke]) -> np.ndarray`：回傳新陣列，不改輸入
  - `def ink_to_png(ink: np.ndarray, dpi: int = 300) -> bytes`（1-bit）

- [ ] **Step 1: Write the failing tests**

```python
def test_detail_controls_sigma(monkeypatch):  # 攔截 extract，確認收到 {"method": "xdog", "sigma": 1.5/1.2/0.8}

def test_render_base_size_and_width():
    img = np.full((1216, 832, 3), 255, np.uint8); cv2.rectangle(img, (200, 300), (600, 900), (0, 0, 0), 6)
    ink, metrics = render_base(img, (2244, 3272), LineDetail.STANDARD, 11)
    assert ink.shape == (3272, 2244) and ink.dtype == bool and metrics["closed_region_count"] >= 1
    # 量矩形上邊的線寬：中間一欄黑色連續像素數在 9–13 之間

def test_apply_strokes_draw_then_erase():
    ink = np.zeros((100, 200), bool)
    out = apply_strokes(ink, [Stroke("draw", 10, ((0.1, 0.5), (0.9, 0.5))), Stroke("erase", 20, ((0.5, 0.5),))])
    assert out[50, 40] and not out[50, 100] and not ink.any()
    # 相對座標：同一筆畫畫在 2 倍大的圖上，位置跟著放大

def test_ink_to_png_is_1bit_300dpi():
    im = Image.open(io.BytesIO(ink_to_png(np.eye(64, dtype=bool))))
    assert im.mode == "1" and round(im.info["dpi"][0]) == 300
```

- [ ] **Step 2: 實作** `render_base`：`extract(color, {"method": "xdog", "sigma": DETAIL_SIGMA[detail]})` → `lineart_post.process(gray, {"target_width": w, "target_height": h, "fit": "contain", "line_width_px": line_width_px})`。`apply_strokes`：座標 × (寬-1, 高-1)，`cv2.polylines` 粗細 `max(1, round(width_px))` 畫在 uint8 遮罩上，每個點再畫實心圓（圓頭、單點也會留下點），`draw` 設 True、`erase` 設 False，依序套用。
- [ ] **Step 3:** 測試 PASS；commit `feat(lineart): render pipeline with detail levels and edit strokes`

---

### Task 5: QA 閉合度

**Files:** Create `backend/app/services/qa.py`；Test `tests/test_qa.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class QAThresholds`: `max_gap_px: int = 40`、`border_px: int = 12`、`gap_error_min: int = 1`、`black_fill_warn: float = 0.005`、`tiny_region_warn: int = 150`、`fragment_warn: int = 80`（數值在 Task 12 校正）
  - `def find_endpoints(ink) -> tuple[np.ndarray, list[tuple[int,int]]]`：(骨架, 端點 (x,y))，離圖邊 `border_px` 內的端點不算
  - `def find_gaps(skel, endpoints, max_gap_px) -> list[tuple[int,int]]`
  - `@dataclass class QAReport`: `errors: list[str]`、`warnings: list[str]`、`gaps: list[tuple[float,float]]`（相對座標）、`bridges`（每個缺口的補線線段，相對座標）、`dangling_count: int`、`gap_count: int`、`near_miss_count: int`、`metrics: dict`。（ruling 2026-11-06：缺口＝補起來會把兩塊 ≥ 3000 px 的白色區域分開的「會漏色」缺口；只是靠近但不封閉的計入 near_miss_count）；`to_dict()` / `from_dict()`
  - `QA_MESSAGES: dict[str, str]`：`"gaps": "線條有缺口，塗色會漏出去，需要修線"`、`"black_fill": "有大片黑色色塊"`、`"tiny_regions": "太多過小的區域，小孩塗不進去"`、`"fragments": "雜點或斷筆偏多"`
  - `def evaluate(ink, line_width_px: int, thresholds: QAThresholds = QAThresholds()) -> QAReport`

- [ ] **Step 1: Write the failing tests**

```python
def ring(gap_px):  # 600×600 白底，畫一個線寬 11 的圓，在右側挖出 gap_px 寬的缺口

def test_closed_shape_has_no_gaps():
    r = evaluate(ring(0), 11); assert r.gap_count == 0 and r.errors == []

def test_small_gap_flagged_once_at_gap_location():
    r = evaluate(ring(20), 11)
    assert r.gap_count == 1 and r.errors == ["gaps"]
    x, y = r.gaps[0]; assert abs(x - right_edge_rel) < 0.05 and abs(y - 0.5) < 0.05

def test_wide_opening_is_not_a_gap():
    assert evaluate(ring(120), 11).gap_count == 0  # 大於 max_gap_px 是刻意的開口

def test_line_ending_near_other_line_is_gap():  # T 字：豎線末端離橫線 15 px → 1 個缺口

def test_free_line_end_is_dangling_not_gap():  # 空白中的一條短線：dangling_count == 2、gap_count == 0

def test_border_endpoints_ignored():  # 一條橫跨整張圖的線：dangling_count == 0

def test_warnings_from_metrics():  # 一塊 200×200 實心黑 → "black_fill" in warnings、不在 errors

def test_report_roundtrip(): assert QAReport.from_dict(r.to_dict()) == r
```

- [ ] **Step 2: 實作 `find_gaps`**（spec §7「兩個線頭距離小於 N px」，延伸到「線頭靠近另一條線」）：

```
對每個端點 e：
  沿骨架（8-連通）從 e 做 BFS，路徑長度上限 3 × max_gap_px，標記 reached
  在 e 周圍半徑 max_gap_px 的圓內，找骨架像素中「不在 reached」的最近一點 q
  有 q → 缺口，標記位置取 e 與 q 的中點
最後合併：彼此距離 < max_gap_px 的標記只留一個（兩個面對面的線頭算一個缺口）
```

  `dangling_count`＝端點數；指標用 `lineart_post.compute_metrics(ink, LineartParams(line_width_px=...))` 的區域、碎片、黑填充，再加 `gap_count`、`dangling_count`。錯誤：`gap_count >= gap_error_min`。警告依門檻。
- [ ] **Step 3:** 用 `experiments/results/phase1_route_c3_final_compare_20261106_092826/raw/` 任一張跑 `evaluate(render_base(...))`，A4 家用尺寸下單頁 < 1.5 秒（寫進 commit message）。
- [ ] **Step 4:** 測試 PASS；commit `feat(qa): endpoint and gap detection with QA report`

---

### Task 6: 資料表欄位（migration 0002）

**Files:** Modify `backend/app/models.py`、`backend/app/storage.py`；Create `backend/migrations/versions/0002_phase3_lineart_qa.py`；Test `tests/test_models_storage.py`、`tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `class CharacterCheck(str, Enum)`: `CONSISTENT="consistent"`, `INCONSISTENT="inconsistent"`
  - `Page` 新欄位：`line_detail: LineDetail = STANDARD`、`line_width_px: int = 11`、`character_check: CharacterCheck | None = None`、`qa_note: str = ""`、`forced_pass_reason: str | None = None`、`passed_at: datetime | None`（UTC 欄位）、`lineart_key: str | None`、`lineart_path: str | None`、`qa_json: str | None`
  - 新表 `LineartEdit`：`generation_id: int`（主鍵、FK generation.id）、`page_id: int`（FK、index）、`strokes_json: str = "[]"`、`updated_at`（UTC）。修改圖層跟著「生成圖」走：換回舊的候選圖時，它的修線也回來。
  - `Storage.lineart(book_id, page_id) -> "books/{b}/pages/{p}/lineart.png"`、`Storage.preview(gen_id, key) -> "cache/previews/{gen_id}_{key}.png"`
- 舊資料的新欄位要有 server_default（`line_detail` → 成員名稱 `'STANDARD'`、`line_width_px` → `11`、`qa_note` → `''`），`test_legacy_db_is_stamped_and_upgraded` 再加斷言：舊頁面升級後 `line_width_px == 11`。

- [ ] **Step 1:** 先寫測試（新欄位預設值、`LineartEdit` 存取、storage 路徑），跑 FAIL。
- [ ] **Step 2:** 改 models、`alembic revision --autogenerate -m "phase3 lineart qa"`，手動補 server_default、改檔名為 `0002_phase3_lineart_qa.py`、revision id `"0002"`。
- [ ] **Step 3:** 全部測試 PASS（含 `test_schema_matches_models`）；commit `feat(db): page lineart/QA fields and lineart edit layer`

---

### Task 7: 頁面線稿服務

**Files:** Create `backend/app/services/page_lineart.py`；Test `tests/test_page_lineart.py`

**Interfaces:**
- Consumes: Task 3 `compute_layout`、Task 4 `render_base / apply_strokes / ink_to_png / RENDER_VERSION`、Task 5 `evaluate / QAReport`、Task 6 欄位
- Produces:
  - `class LineartUnavailable(Exception)`：`.message` 給使用者看（「這一頁還沒有選定圖片」「選定的圖片檔案遺失，請重新選圖或重新生成」）
  - `def load_strokes(session, gen_id: int) -> list[Stroke]`、`def save_strokes(session, page_id: int, gen_id: int, strokes: list[Stroke]) -> None`（不 commit）
  - `def lineart_key(gen: Generation, page: Page, content_px, strokes_json: str) -> str`：sha256 前 16 字元，輸入含 `RENDER_VERSION`、gen.id、gen.image_path、line_detail、line_width_px、content_px、strokes_json
  - `@dataclass class PageLineart`: `path: str`、`key: str`、`qa: QAReport`
  - `def ensure_page_lineart(session, storage, page_id: int) -> PageLineart`：key 一樣且檔案在 → 直接回傳快取（不重算）；否則重算、寫 PNG、以條件式 UPDATE（`selected_generation_id` 沒變）存 `lineart_key / lineart_path / qa_json`；若頁面是 `passed` 且新 QA 有錯 → `draft`、清 `passed_at`。rowcount 0（期間被換圖）→ 用新狀態重來一次，最多 3 次。
  - `def render_preview(storage, gen: Generation, content_px, detail, line_width_px) -> str`：沒有修改圖層的預覽，快取到 `Storage.preview`

- [ ] **Step 1: Write the failing tests**（用 `make_png` 產生有黑框的小彩圖當選定圖）

```python
def test_cached_until_inputs_change(...)      # 第二次呼叫不呼叫 render_base（monkeypatch 計數）；改線寬後重算、key 改變
def test_strokes_change_key_and_output(...)   # 加一筆 draw 後 PNG 內容改變
def test_passed_page_with_new_errors_demoted(...)  # 手動把頁設 passed，讓新結果有缺口 → draft
def test_passed_page_without_errors_kept(...)
def test_no_selection_raises(...)            # LineartUnavailable，訊息「這一頁還沒有選定圖片」
def test_missing_image_raises(...)           # 刪掉圖片檔 → LineartUnavailable，不是 FileNotFoundError
```

- [ ] **Step 2:** 實作；圖片用 `lineart_post.load_image(storage.abs(gen.image_path))` 讀。
- [ ] **Step 3:** 測試 PASS；commit `feat(lineart): cached page lineart with QA and status demotion`

---

### Task 8: 線稿、修改圖層、QA API

**Files:** Create `backend/app/api/lineart.py`；Modify `backend/app/api/schemas.py`、`backend/app/api/pages.py`（`PageUpdate`、`PageOut`、`select_generation`）、`backend/app/main.py`；Test `tests/test_api_lineart.py`

**Interfaces（路徑）:**
- `GET /api/pages/{id}/lineart` → 1-bit PNG（內容範圍）；沒選圖或圖檔遺失 → 409 + `LineartUnavailable.message`
- `GET /api/pages/{id}/qa` → `QAOut`：`status`、`errors / warnings: list[{code, message}]`、`gaps: list[[x,y]]`、`gap_count`、`dangling_count`、`metrics`、`character_check`、`qa_note`、`forced_pass_reason`、`lineart_url`（帶 `?v={key}`）
- `GET /api/pages/{id}/qa/overlay.png` → RGB：線稿 + 每個缺口紅色空心圓（半徑 40 px、線寬 6）；印刷廠模式再畫裁切框（藍、4 px）與安全區（綠、4 px）
- `GET /api/pages/{id}/lineart/edits` → `{generation_id, strokes}`；`PUT` 同樣格式 → 回 `QAOut`
- `POST /api/pages/{id}/lineart/autofix` `{generation_id}` → 對目前 QA 的每個 `bridges` 線段，在該圖的修改圖層**最後面**加一筆 `draw`（`width_px`＝頁面 `line_width_px`），重算 QA；重複最多 `AUTOFIX_MAX_PASSES = 3` 輪，直到沒有缺口或某輪缺口數沒減少（實測一輪只能從 60→4、113→33，因為補線會讓別的近缺口變成缺口）；全部筆畫一次存檔，回 `QAOut`（加 `autofix_added: int`）；generation_id 不是目前選定 → 409；沒有缺口 → 200 且不新增筆畫（ruling 2026-11-06：實測每頁 13–113 個真缺口，全靠手動修不可行；筆畫進修改圖層，使用者可以復原或擦掉）
- `GET /api/generations/{id}/lineart-preview?detail=standard&line_width_px=11` → PNG（角色或頁面候選都可；未成功 → 404）
- `POST /api/books/{id}/reference-sketch`（上傳檔案）→ PNG：xdog sigma 1.2 擷取、以 `contain` 放進該書 `generation_size`、白底黑線；給前端放進畫板，不存檔
- `PATCH /api/pages/{id}` 加 `line_detail`、`line_width_px`（`ge=8, le=16`）
- `PageOut` 加：`line_detail`、`line_width_px`、`character_check`、`lineart_url`（有選圖才有）、`needs_fix: bool | None`（`qa_json` 有錯誤；沒算過為 None）、`aspect_mismatch: bool`（選定圖寬高 ≠ 繪本目前 `generation_size`）；`sketch_url` 加 `?v={updated_at 時間戳}` 防快取
- 修改圖層驗證（Pydantic）：`op` 只允許 draw/erase；`width_px` 2–64；每點兩個有限浮點數且在 [0, 1]；每筆 1–2000 點；最多 500 筆；全部最多 20000 點

**狀態規則（spec §7、作者決定）：** 改 `line_detail / line_width_px`、PUT 修改圖層或 autofix → `passed` 退回 `draft`（`character_check` 保留，彩圖沒變）。`select_generation` 換了圖 → 退回 `draft` 並清掉 `character_check`、`forced_pass_reason`。

- [ ] **Step 1: Write the failing tests**

```python
def test_lineart_png_and_qa(client, app)          # 選圖後 GET lineart：200、mode "1"、尺寸＝content_px；GET qa 有 gap_count
def test_lineart_requires_selection(client, app)  # 409「這一頁還沒有選定圖片」
def test_params_change_rerenders_and_demotes(...) # PATCH line_width_px=14 → lineart_url 的 v 改變；passed → draft
def test_line_width_range(client)                 # 7、17 → 422
def test_edits_roundtrip_and_apply(...)           # PUT 一筆 draw → GET edits 一樣；lineart 改變
def test_edits_reject_stale_generation(...)       # generation_id 不是目前選定 → 409，LineartEdit 沒有新列
def test_edits_validation(...)                    # 座標 1.5、NaN（以字串 "NaN" 送）、20001 點、op "fill" → 422
def test_edits_follow_generation(...)             # 圖 A 修線 → 選圖 B（沒有修線）→ 選回 A，修線還在
def test_autofix_appends_bridges(...)              # 線稿有缺口（monkeypatch evaluate 或用有缺口的圖）→ autofix 後筆畫數 += gap 數、gap_count 變 0；stale generation_id → 409
def test_select_resets_character_check(...)
def test_overlay_png(...)                          # RGB、尺寸＝content_px
def test_generation_preview(client, app)           # 角色候選可預覽；排隊中 → 404
def test_reference_sketch(client)                  # 上傳 300×200 黑框圖 → PNG 尺寸＝generation_size；非圖片 → 400
def test_page_out_aspect_and_sketch_version(...)
```

- [ ] **Step 2:** 實作；同步端點（`def`）讓 FastAPI 放進 threadpool。PUT edits：先檢查 `body.generation_id == page.selected_generation_id`，`save_strokes` 與「頁面仍選定這張」的條件式 UPDATE（同時處理狀態退回）在同一個交易，rowcount 0 → rollback + 409。
- [ ] **Step 3:** 測試 PASS；commit `feat(api): lineart, edit layer, QA and previews`

---

### Task 9: 角色檢查與頁面通過

**Files:** Create `backend/app/api/review.py`；Modify `backend/app/main.py`；Test `tests/test_api_review.py`

**Interfaces:**
- `POST /api/pages/{id}/character-check` `{result: "consistent"|"inconsistent", note: str(≤500, strip)}` → `PageOut`；`passed` 頁標為不一致 → `draft`
- `POST /api/pages/{id}/pass` `{force_reason: str | None}`：
  1. 沒選圖 → 409「這一頁還沒有選定圖片」
  2. `character_check != consistent` → 409「請先確認角色外型與設定一致」（不可強制）
  3. `ensure_page_lineart`；有錯誤且 `force_reason` 去空白後為空 → 409，`detail = {"message": "自動檢查有錯誤，修線後再通過，或填寫原因強制通過", "errors": [...]}`
  4. 否則 `passed`、`passed_at = utcnow()`、`forced_pass_reason`＝原因（無錯時存 None）。以條件式 UPDATE 確認 `selected_generation_id` 與 `lineart_key` 沒變，否則 409「頁面剛被修改，請重新檢查」
- `POST /api/pages/{id}/unpass` → `draft`

- [ ] **Step 1: Write the failing tests**：上述每個分支一個測試，加 `test_force_pass_records_reason`、`test_pass_rejected_if_lineart_changed_concurrently`（monkeypatch `ensure_page_lineart` 在回傳前改 page 線寬）。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(api): character check and page pass/unpass`

---

### Task 10: 繪本改尺寸／列印方式

**Files:** Modify `backend/app/api/books.py`、`backend/app/api/schemas.py`（`BookUpdate`）；Test `tests/test_api_books.py`

**Interfaces:**
- `PATCH /api/books/{id}` 加 `paper_size`、`print_mode`（都選填）。新尺寸與原尺寸 `ratio_group` 不同 → 422「只能改成同比例的紙張尺寸（…）」。
- 有改尺寸或列印方式 → 回應後以 FastAPI `BackgroundTasks` 對每個有選圖的頁面呼叫 `ensure_page_lineart`（新 session；單頁失敗只記 log）。快取 key 含 `content_px`，所以就算背景工作還沒跑完，任何讀取也會拿到新尺寸的結果。

- [ ] **Step 1: Write the failing tests**

```python
def test_change_size_same_group_only(client)      # A4 → A5 OK；A4 → letter 422；print_mode 可任意改
def test_change_size_recomputes_and_demotes(client, app)
    # 兩頁都 passed（一頁的修線會在新尺寸下留下缺口、一頁乾淨）；PATCH print_mode
    # → TestClient 會在回應後跑完背景工作；有缺口的頁 draft、乾淨的仍 passed；
    #   lineart 尺寸＝新 content_px；修線位置依相對座標換算
```

- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(api): change paper size/print mode with lineart recompute`

---

### Task 11: 匯出

**Files:** Create `backend/app/services/export.py`、`backend/app/api/export.py`；Modify `backend/app/main.py`、`README.md`；Test `tests/test_export.py`、`tests/test_api_export.py`

**Interfaces:**
- `def compose_page(ink: np.ndarray, layout: Layout) -> Image.Image`：mode `"1"`、大小 `layout.page_px`、內容貼在 `content_offset_px`
- `def build_pdf(pages: list[Image.Image], dpi: int = 300) -> bytes`：Pillow `save(..., "PDF", resolution=dpi, save_all=True)`
- `GET /api/books/{id}/export/summary` → `{total, passed, exportable_ids, skipped: [{page_id, position, reason}]}`（reason：`"no_selection"`、`"not_passed"`）
- `GET /api/books/{id}/export.pdf?include=passed|all`（預設 passed；all＝所有有選圖的頁）→ `application/pdf`，`Content-Disposition` 用 RFC 5987 `filename*`（書名可為中文）；沒有可匯出的頁 → 409「沒有可匯出的頁面（需要先通過 QA）」；某頁 `LineartUnavailable` → 409，訊息帶頁碼
- `GET /api/pages/{id}/print.png` → 單頁完整版面 1-bit PNG、300 DPI

- [ ] **Step 1: Write the failing tests**

```python
def test_compose_home_a4():  # page (2480, 3508)；內容左上角黑點出現在 (118, 118)
def test_pdf_mediabox():     # A4 家用 → b"/MediaBox [ 0 0 595.2 841.92 ]"；A4 印刷廠 → 612.24 858.96；頁數正確
def test_export_passed_only_and_order(client, app)  # 3 頁、2 頁 passed、依 position 排序 → PDF 2 頁；include=all → 3 頁
def test_export_errors(client, app)                  # 沒有 passed → 409；選定圖檔刪除 → 409 且訊息含頁碼
def test_summary_reasons(client, app)
def test_print_png(client, app)                      # 尺寸＝page_px、dpi 300、mode "1"
```

- [ ] **Step 2:** 實作；**Step 3:** README 的 API 表補上 Phase 3 端點與「線稿與 QA」一節（細緻度、線寬、修改圖層格式、通過規則、PDF 版面）；全部測試 PASS；commit `feat(export): book PDF and single-page print PNG`

---

### Task 12: 校正與 GPU 實測（controller 親自做）

不派 subagent；需要看圖判斷。開 GPU 前先 `nvidia-smi` 確認沒有其他任務，有的話跳過 GPU 部分並在報告註明。

- [ ] **Step 1: QA 門檻校正（不需 GPU）**：對 `phase1_route_c3_final_compare_*/raw/` 的圖跑 A4 家用 standard／11 px，輸出 overlay 與 `gap_count / dangling_count / tiny_region_count / fragment_count / black_fill_ratio` 到 `experiments/results/phase3_qa_calibration_<ts>/`（腳本放 `experiments/qa_calibrate.py`，含測試）。看 overlay：紅圈標到的是不是真的會漏色的缺口？調 `max_gap_px` 與警告門檻，寫 NOTES.md（各門檻的依據）。
- [ ] **Step 2: 角色提示詞**（GPU）：目前角色圖是半身、米色背景。比較 3 組提示詞 × 4 個 seed（例如：加強 `full body, standing, feet visible`，負向加 `close-up, portrait, upper body, cropped, colored background, beige background`；以及把「full body」移到最前面）。看圖選出全身、白底比例最高的一組，更新 `prompts.py` 與 `test_prompts.py`；紀錄到 `experiments/results/phase3_character_prompt_<ts>/NOTES.md`。
- [ ] **Step 3: 端到端**（GPU）：啟動後端（`APP_COMFY__URL=http://127.0.0.1:8190`、暫用 storage），用 API 建 A4 印刷廠繪本 → 角色 → 一頁 → 選圖 → 線稿 → QA → 修一筆 → 角色一致 → 通過（必要時強制）→ 匯出 PDF；改成 A5 再匯出。確認 PDF 頁面尺寸與線寬。
- [ ] **Step 4:** commit 校正與實驗紀錄 `chore: Phase 3 QA calibration and character prompt tuning`

---

## Self-Review 紀錄

- Spec 覆蓋：§6.1 換尺寸（Task 10）、參考線（Task 3、8 overlay）；§6.2 線稿預覽（Task 8 preview）；§6.3 參數即時重擷取（Task 7、8）、上傳參考圖轉線條（Task 8）；§6.5 匯出與單頁 PNG（Task 11）；§7 閉合度、紅點、警告、角色人工檢查、狀態與強制通過（Task 5、8、9）；§8 修改圖層（Task 4、6、8）。畫板本身與 WebSocket 進度屬 Phase 4。
- 型別一致：`LineDetail`、`Stroke`、`QAReport`、`LineartUnavailable`、`ensure_page_lineart` 在 Task 4–11 名稱一致。
