# ComfyUI 著色頁節點包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把網站的單頁流程做成 ComfyUI 節點包 `ComfyUI-ColoringBook`（12 顆節點＋兩個範例 workflow），可上架 Comfy Registry。

**Architecture:** 新的獨立 repo `/ai-data/repos/ComfyUI-ColoringBook`。`coloring_core/` 是從網站複製的純影像程式（不 import torch、不 import ComfyUI）；
`nodes/` 是薄薄一層 V3 節點（`comfy_api.latest`），只做張量 ↔ numpy 轉換、批次、輸入驗證，然後呼叫 `coloring_core`。
生成部分不寫節點，範例 workflow 直接用內建節點＋ComfyUI-GGUF。網站 repo 只新增一支產生對照資料的腳本。

**Tech Stack:** Python 3.11+、numpy、opencv-python-headless、Pillow、scikit-image、torch（ComfyUI 提供）、ComfyUI 0.36 V3 節點 API、pytest。

**Spec:** `docs/superpowers/specs/2026-11-13-comfy-nodepack-design.md`（網站 repo；執行前兩份都要讀）

## Global Constraints

- 網站 repo（`/ai-data/repos/coloring-books`）的程式、測試、資料庫都不改；唯一新增 `scripts/comfy_parity_refs.py`（只讀網站程式）。
- 節點包 repo：`/ai-data/repos/ComfyUI-ColoringBook`，授權 GPL-3.0；Registry 名稱 `comfyui-coloringbook`；顯示名稱 `Coloring Book Page Maker`；`requires-comfyui = ">=0.36.0"`。
- 節點 id 前綴 `CB`、分類 `Coloring Book`；英文名稱，附 `locales/zh-TW/main.json`。
- 只做家用版：`PrintMode.HOME`（四邊 10 mm 白邊）。紙張：A4／A5／B5／Letter／正方形 21 cm。線寬 8–16（預設 11）。線條細節 simple／standard／detailed（預設 standard）。
- 自動合併外框（`outline_fill`）、自動補線預設開。角色位置預設 cx 0.5、cy 0.66、height 0.6、flip False。
- 角色動作生成固定 832×1216；背景生成尺寸 = `compute_layout(paper, HOME).generation_size`。
- 不附模型、不連網、不下載任何東西。
- `coloring_core` 不得 import torch 或 ComfyUI；`nodes/` 不得重寫影像演算法，只能呼叫 `coloring_core`。
- 張量 → uint8 一律用 ComfyUI SaveImage 的截斷：`np.clip(t * 255.0, 0, 255).astype(np.uint8)`（網站拿到的就是 SaveImage 存出的 PNG，這樣同一張生成結果合成出來才會相同）。黑白 IMAGE：墨水 = 0.0，白 = 1.0，三通道相同；轉回 ink 用 `< 0.5`。
- 錯誤訊息：英文短句 + 「：」+ 網站原本的中文訊息，丟 `ValueError`。
- ComfyUI 內建節點名稱與輸入欄位一律以執行中 ComfyUI 的 `/object_info` 為準，不猜。
- 測試不需要 GPU。兩個 venv：
  - **core venv** `/ai-data/venvs/ubuntu24.04/comfyui-coloringbook`：固定 `numpy==2.5.4 opencv-python-headless==5.0.0.93 pillow==13.0.0 scikit-image==0.26.0 pytest==9.1.1`（＝網站 venv），跑 `tests/core/`，對照測試在這裡必須逐像素相同。
  - **comfy venv** `~/tools/comfy-cli-venv`（使用者已同意 pip 安裝 scikit-image、pytest 等缺的套件），以 `COMFYUI_PATH=~/projects/comfyui` 跑 `tests/nodes/`；沒有 `comfy_api` 時整個目錄 skip。
- 執行 pytest 前 `env -u PYTHONPATH`（ROS 的 PYTHONPATH 會壞掉收集）。
- GPU 步驟（Task 9）與對外動作（Task 10）都要先等使用者說可以。

## Review Focus

1. 上傳的背景是 RGBA／灰階／很小（100×100）的圖 → 鋪白後 cover_crop 到生成尺寸，不當機、透明處不是黑色（Task 5 `test_switch_uploaded_rgba_small`）。
2. BiRefNet 回傳全黑或細長的遮罩 → 節點丟出含中文原訊息的 ValueError，不是 numpy 例外（Task 6 `test_clean_mask_empty`、`test_composite_degenerate_mask`）。
3. 批次大小不一致（背景 2 張、角色 3 張）→ 清楚的錯誤；其中一個是 1 張時套用到全部（Task 3 `test_broadcast_*`，Task 6 背景 2 張＋角色 1 張）。
4. 文字只有空白、含換行或零寬字元、長到放不下 → 正規化成「沒有字」或自動縮字，不當機；兩行都關 → 空白文字帶（Task 7 `test_layout_whitespace_caption`、`test_layout_long_caption`）。
5. 角色放到幾乎出界（cx 0、cy 1、height 1.0、flip）→ 照網站裁掉超出部分，輸出尺寸＝背景尺寸（Task 6 `test_composite_offcanvas`）。

---

### Task 1：建立 repo 並複製 coloring_core

**Files:**
- Create: `/ai-data/repos/ComfyUI-ColoringBook/`（`git init`，分支 `main`）
- Create: `coloring_core/{__init__,imageio,layout,lineart_params,lineart_extract,lineart_post,lineart_render,qa,autofix,compose,captions,caption_text,export,prompts,images}.py`
- Create: `coloring_core/SOURCE.md`、`fonts/NotoSansTC-VF.ttf`、`fonts/OFL.txt`、`LICENSE`（GPL-3.0 全文）、`.gitignore`
- Create: `requirements.txt`（`opencv-python-headless>=4.10`、`scikit-image>=0.24`）、`requirements-dev.txt`（上方固定版本＋`-r requirements.txt`）
- Create: `pyproject.toml`、`tests/core/conftest.py`、`tests/core/test_*.py`（複製）

**Interfaces:**
- Produces（後面所有 task 用）：
  - `coloring_core.layout`: `PaperSize`、`PrintMode`、`compute_layout(size, mode, dpi=300) -> Layout`
  - `coloring_core.lineart_render`: `LineDetail`、`render_lineart(color: np.ndarray, content_px, detail, line_width_px, *, outline_fill=False) -> np.ndarray[bool]`、`Stroke`、`apply_strokes`
  - `coloring_core.compose`: `Placement`、`DEFAULT_PLACEMENT`、`compose(bg, pose, mask, placement) -> PIL.Image`、`largest_component(mask_png: bytes) -> bytes`、`DEGENERATE_MASK`
  - `coloring_core.export`: `layout_print_page(ink, layout, captions, font_path, show) -> PIL.Image("1")`、`png_bytes(page, dpi=300) -> bytes`
  - `coloring_core.prompts`: 網站 `prompts.py` 全部，另加 `EXPRESSIONS`、`FACINGS`（`tuple[tuple[str, str], ...]`，(中文標籤, 英文片語)，值照 `frontend/src/lib/actions.ts`）與 `append_phrase(text: str, phrase: str) -> str`（照 `appendPhrase`：已有（整詞、不分大小寫）就不加；去掉結尾逗號後以 `", "` 連接；空字串直接回 phrase）
  - `coloring_core.images`: `cover_crop(img, size) -> tuple[Image, box]`、`letterbox(...)`（照網站 `uploads.py`，不帶 FastAPI 的部分）、`flatten_on_white`（re-export 自 imageio）
  - `coloring_core.autofix`: 另加 `close_gaps_auto(ink: np.ndarray, line_width_px: int) -> np.ndarray`：`evaluate` → 有 bridges 時 `plan_autofix(ink, [], gap_count, bridges, width, room_strokes=MAX_STROKES, room_points=MAX_TOTAL_POINTS)` → `apply_strokes(ink, auto)`（＝網站 pipeline 步驟 3–4 在沒有 user 層時的結果）
  - `coloring_core.FONT_PATH: Path`（`fonts/NotoSansTC-VF.ttf`）

- [ ] **Step 1：** 建 repo、core venv（`python3.11 -m venv /ai-data/venvs/ubuntu24.04/comfyui-coloringbook && pip install -r requirements-dev.txt`）。
- [ ] **Step 2：** 從網站 master（記下 `git -C /ai-data/repos/coloring-books rev-parse master`）複製上列模組，把 `from app.services` / `import app.services` 改成 `coloring_core`。`lineart_render` 的 `ink_to_png` re-export 刪掉。`images.py` 只放 `cover_crop`、`letterbox`。字型與 OFL.txt 從 `assets/fonts/` 複製。
- [ ] **Step 3：** 複製 `tests/test_{layout,lineart_post,lineart_render,lineart_extract,qa,autofix,export,prompts}.py` 與 `test_compose.py`、`test_captions.py` 中**不需要** `app.models`／`app.api`／`app.main`／`app.config`／`tests.fake_engine`／`experiments` 的測試函式到 `tests/core/`，匯入改成 `coloring_core`；需要的 helper 一起搬進 `tests/core/conftest.py`。被丟掉的測試名稱列在 `SOURCE.md`（含原因）。
- [ ] **Step 4：** 新增 `tests/core/test_prompts_extra.py`：
```python
def test_append_phrase():
    assert append_phrase("waving one hand", "with a happy smiling expression") == "waving one hand, with a happy smiling expression"
    assert append_phrase("waving, ", "facing the viewer") == "waving, facing the viewer"
    assert append_phrase("Waving and Facing The Viewer", "facing the viewer") == "Waving and Facing The Viewer"
    assert append_phrase("", "facing the viewer") == "facing the viewer"
def test_phrase_tables():
    assert len(EXPRESSIONS) == 8 and EXPRESSIONS[0] == ("開心", "with a happy smiling expression")
    assert [p for _, p in FACINGS] == list(FACING_PHRASES)
def test_close_gaps_auto_matches_manual(sample_ink):  # 用 test_autofix 的同一張測試圖
    ...  # 與手動 evaluate + plan_autofix + apply_strokes 結果 array_equal；沒有缺口時原樣回傳
def test_font_path_exists():
    assert FONT_PATH.is_file()
def test_core_has_no_torch_or_comfy_import():  # 掃 coloring_core/*.py 的原始碼
    ...  # 不得出現 "import torch"、"comfy"
```
- [ ] **Step 5：** `env -u PYTHONPATH <core venv>/bin/pytest tests/core -q` → 全部 PASS。
- [ ] **Step 6：** `SOURCE.md`：來源 commit、檔案對照表（網站路徑 → 這裡的路徑）、改動清單、丟掉的測試。`pyproject.toml` 填 `[project]`（name `comfyui-coloringbook`、version `0.1.0`、license `GPL-3.0-only`、dependencies 同 requirements.txt）與 `[tool.comfy]`（`PublisherId = ""` 留空，Task 10 填；`DisplayName`；`includes = ["fonts"]`），`[tool.pytest.ini_options] testpaths = ["tests"]`。
- [ ] **Step 7：** Commit `feat(core): copy image pipeline from coloring-books <sha>`。

### Task 2：對照資料（網站產生、節點包驗證）

**Files:**
- Create（網站 repo）: `scripts/comfy_parity_refs.py`
- Create（節點包）: `tests/fixtures/parity/`（輸入與參考輸出）、`tests/core/test_parity.py`

**Interfaces:**
- Consumes: Task 1 的 `coloring_core.*`
- Produces: `tests/fixtures/parity/manifest.json`：`{"cases": [{"name", "inputs": {...}, "params": {...}, "outputs": {...}}]}`，Task 4–7 的節點層測試也讀它。

- [ ] **Step 1：** 寫 `scripts/comfy_parity_refs.py --out <dir>`（用網站 venv 執行，只 import `app.services.*`）。輸入固定為 `experiments/results/phase14_single_outline_20261113_1009/input/p01.png`（當角色）與 `p02.png`（當背景）；遮罩＝p01 非白像素（任一通道 < 245）→ 255，存成 L PNG。產生並存檔：
  - `mask_clean.png` = `largest_component(mask.png)`
  - `composite.png` = `compose(p02, p01, mask_clean, DEFAULT_PLACEMENT)`，另一組 `composite_flip.png`（cx 0.3, cy 0.7, height 0.8, flip True）
  - `ink_a4.png` = `render_lineart(composite, A4 HOME content_px, STANDARD, 11, outline_fill=True)`；`ink_a4_nofill.png`（outline_fill False）；`ink_letter_detailed.png`（Letter、DETAILED、14、True）
  - `ink_a4_fixed.png` = 網站 pipeline 的補線結果（evaluate＋plan_autofix＋apply_strokes，與 Task 1 `close_gaps_auto` 相同算法，但這裡用網站的模組）
  - `page_a4_both.png` = `layout_print_page(ink_a4_fixed, A4 HOME, ("小兔子去野餐", "The bunny goes on a picnic"), font, (True, True))`；`page_a4_blank.png`（兩行都關）；`page_a4_none.png`（兩行都開但都是 None → 沒有文字帶）
  - `bg_cover.png` = `cover_crop(p01 RGBA 版（四角透明）鋪白, (832, 1216))`
  - `prompts.json`：三種樣板各 2 組輸入的完整提示詞（含表情＋面向）
  - `manifest.json` 記錄每個輸出的參數與網站 commit
- [ ] **Step 2：** 執行腳本輸出到節點包 `tests/fixtures/parity/`，確認總大小 < 15 MB（1-bit PNG 很小；彩色輸入 2 張）。
- [ ] **Step 3：** `tests/core/test_parity.py`：對 manifest 每一筆用 `coloring_core` 重算，`np.array_equal`（彩色比 RGB 陣列、1-bit 比 ink、PNG bytes 不比）。提示詞比字串。
- [ ] **Step 4：** core venv 跑 → PASS。
- [ ] **Step 5：** 兩個 repo 各自 commit（網站：`chore(scripts): parity reference generator for ComfyUI node pack`，在分支 `phase15-comfy-nodepack`）。

### Task 3：節點骨架與張量轉換

**Files:**
- Create: `__init__.py`、`nodes/__init__.py`、`nodes/convert.py`、`nodes/paper.py`、`tests/nodes/conftest.py`、`tests/nodes/test_convert.py`、`tests/nodes/test_registration.py`

**Interfaces:**
- Produces:
  - `nodes.convert`: `image_to_rgb_u8(t: Tensor[H,W,C]) -> np.ndarray[H,W,3] uint8`（截斷規則見 Global）、`rgb_u8_to_image(a) -> Tensor[1,H,W,3]`、`mask_to_u8(m: Tensor[H,W]) -> np.ndarray uint8`、`u8_to_mask(a) -> Tensor[1,H,W]`、`ink_to_image(ink: bool[H,W]) -> Tensor[1,H,W,3]`、`image_to_ink(t: Tensor[H,W,C]) -> bool[H,W]`、`broadcast(*batches: Tensor) -> list[tuple[Tensor, ...]]`（逐張配對；長度 1 的套用到全部；其他長度不一致丟 `ValueError("Batch sizes do not match：批次張數不一致 (a, b, …)")`）
  - `nodes.paper`: `@dataclass(frozen=True) PaperSpec(paper: PaperSize, line_width_px: int, detail: LineDetail)`，`.layout -> Layout`（HOME）、`.generation_size -> tuple[int, int]`；`PAPER = io.Custom("CB_PAPER")`
  - 根目錄 `__init__.py`：`ColoringBookExtension(ComfyExtension)`，`get_node_list()` 回傳 12 個節點類別；`async def comfy_entrypoint() -> ColoringBookExtension`
- [ ] **Step 1：** `tests/nodes/conftest.py`：讀 `COMFYUI_PATH`，加進 `sys.path`，`pytest.importorskip("comfy_api.latest")`；把節點包根目錄加進 `sys.path`。
- [ ] **Step 2：** 測試：轉換來回（uint8 → tensor → uint8 不變；0.99999*255 截成 254）、ink ↔ IMAGE、broadcast（1 對 3、3 對 3、2 對 3 錯誤）、PaperSpec A4 → layout.content_px == compute_layout(A4, HOME).content_px、generation_size A4 (832,1216) / LETTER (896,1152) / SQUARE_21 (1024,1024)。
- [ ] **Step 3：** 實作（V3：參考 `~/projects/comfyui/comfy_extras/nodes_bg_removal.py` 的寫法）。
- [ ] **Step 4：** `test_registration.py`：`comfy_entrypoint()` 的節點清單 node_id 集合＝12 個預期 id（`CBCharacterPrompt CBBackgroundPrompt CBPosePrompt CBLoadCharacter CBBackgroundSwitch CBCleanMask CBComposite CBPaper CBLineArt CBCloseGaps CBPageLayout CBSavePrintPNG`），每個 schema 的 category == "Coloring Book"。此時尚未實作的節點先不列入，各 task 完成時把 id 加進測試的預期集合，Task 7 結束時必須是 12 個。
- [ ] **Step 5：** comfy venv 先 `pip install scikit-image pytest`，再 `env -u PYTHONPATH COMFYUI_PATH=~/projects/comfyui ~/tools/comfy-cli-venv/bin/python -m pytest tests/nodes -q` → PASS；core venv 跑 `tests/` → nodes 全部 skip、core PASS。
- [ ] **Step 6：** Commit。

### Task 4：提示詞節點與紙張節點

**Files:**
- Create: `nodes/prompts.py`、`tests/nodes/test_prompt_nodes.py`、`tests/nodes/test_paper_node.py`
- Modify: `nodes/__init__.py`（註冊）

**Interfaces:**
- Consumes: `coloring_core.prompts`、`nodes.paper.PaperSpec`
- Produces: `CBCharacterPrompt(description, variation) -> STRING`；`CBBackgroundPrompt(scene, variation) -> STRING`；`CBPosePrompt(action, expression, facing, variation) -> STRING`；`CBPaper(paper, line_width, line_detail) -> (CB_PAPER, width INT, height INT)`
- 下拉值：variation `["none", *對應 VARIATIONS]`；expression `["none", *EXPRESSIONS 的英文片語]`、facing `["none", *FACINGS 的英文片語]`（中文標籤放 locales）；paper `["A4","A5","B5","Letter","Square 21cm"]`；line_detail `["simple","standard","detailed"]`。

- [ ] **Step 1：** 測試：
  - 主角／背景：`variation="none"` 時輸出 == `qwen_character_prompt(desc)`／`qwen_background_prompt(scene)`；選了 == 帶 variation 的版本；對 `prompts.json` fixture 逐字相同。
  - 動作：`action="waving one hand", expression=開心片語, facing="facing the viewer", variation="none"` → `qwen_pose_prompt(append_phrase(append_phrase(action, expr), facing))`；facing 選了且 variation 是前兩個轉向語 → `ValueError`（中文：「選了面向就不能用轉向變化語」）。
  - 空白描述／場景／動作 → `ValueError`（中文：「請輸入…」）。
  - CBPaper：A4、11、standard → PaperSpec 對、width/height 832/1216；line_width 7 或 17 由 schema 範圍擋（測 schema 的 min/max）。
- [ ] **Step 2：** 跑 → FAIL。
- [ ] **Step 3：** 實作；description／scene／action 為 multiline String。
- [ ] **Step 4：** 跑 → PASS；更新 registration 預期集合。
- [ ] **Step 5：** Commit。

### Task 5：載入主角與背景來源

**Files:**
- Create: `nodes/inputs.py`、`tests/nodes/test_load_character.py`、`tests/nodes/test_background_switch.py`

**Interfaces:**
- Consumes: `coloring_core.images.cover_crop`、`flatten_on_white`、`nodes.convert`、`PaperSpec`
- Produces:
  - `CBLoadCharacter(image: COMBO) -> IMAGE`：選項＝`output/coloring_book/` 下檔名以 `character` 開頭的圖（新到舊，值加前綴 `output/`）＋`input/` 的圖（值加前綴 `input/`，同 LoadImage 的清單規則）；有上傳按鈕（上傳到 input）。讀檔 → `flatten_on_white` → RGB → IMAGE `[1,H,W,3]`。`fingerprint_inputs`／IS_CHANGED 用檔案 mtime+size。路徑解析拒絕 `..` 與絕對路徑。
  - `CBBackgroundSwitch(paper: CB_PAPER, generated: IMAGE lazy, uploaded: IMAGE optional lazy) -> IMAGE`：`check_lazy_status` 有接 uploaded 時只要 `["uploaded"]`，否則 `["generated"]`；uploaded 逐張 `cover_crop` 到 `paper.generation_size`；generated 原樣通過（尺寸不同時也 cover_crop）。
- [ ] **Step 1：** 先在 0.36 原始碼確認兩件事並寫進 `nodes/inputs.py` 的註解：(a) V3 `io.Combo.Input` 的上傳／資料夾參數怎麼宣告（若 V3 不支援，CBLoadCharacter 改用 V1 類別寫法，參考 `nodes.py` 的 `LoadImage`、`LoadImageOutput`）；(b) `check_lazy_status` 收到的未連線 optional 輸入與未評估 lazy 輸入各是什麼值（看 `execution.py`）。
- [ ] **Step 2：** 測試（用 `tmp_path` 假造 output/input 目錄，monkeypatch `folder_paths.get_output_directory`／`get_input_directory`）：
  - 清單：`character_00002_.png` 排在 `character_00001_.png` 前；`pose_00001_.png` 不出現；input 的圖帶 `input/` 前綴。
  - 透明 PNG（RGBA，四角 alpha 0）→ 四角像素 == (255,255,255)。
  - `../x.png`、`/etc/passwd` → `ValueError`。
  - Switch：`check_lazy_status` 有 uploaded 連線 → `["uploaded"]`；沒有 → `["generated"]`。
  - `test_switch_uploaded_rgba_small`（Review Focus 1）：上傳背景經 LoadImage 進來時已是 RGB（alpha 被丟掉），所以測 100×100 RGB 與灰階擴成 3 通道的 IMAGE → 輸出 832×1216、與 `cover_crop` 結果 array_equal。README 提醒透明背景請先鋪白。
  - 上傳圖 == fixture `bg_cover.png` 的來源時，輸出與參考相同。
- [ ] **Step 3：** 跑 → FAIL；實作；跑 → PASS；更新 registration。
- [ ] **Step 4：** Commit。

### Task 6：遮罩清理與合成

**Files:**
- Create: `nodes/compose.py`、`tests/nodes/test_compose_nodes.py`

**Interfaces:**
- Consumes: `coloring_core.compose`、`nodes.convert`
- Produces: `CBCleanMask(mask: MASK) -> MASK`（逐張：mask → uint8 L PNG bytes → `largest_component` → MASK）；`CBComposite(background: IMAGE, character: IMAGE, mask: MASK, x=0.5, y=0.66, size=0.6, flip=False) -> IMAGE`（逐張 broadcast；`Placement` 驗證錯誤轉成節點錯誤；size 範圍 0.1–1.0）
- [ ] **Step 1：** 測試：
  - fixture：`mask.png` → CleanMask → == `mask_clean.png`；(p02, p01, mask_clean, 預設) → Composite == `composite.png`；flip 組 == `composite_flip.png`。
  - `test_clean_mask_empty`（Review Focus 2）：全 0 遮罩 → 全 0（照網站），接著 Composite → `ValueError` 含「遮罩是空的」。
  - `test_composite_degenerate_mask`：1×200 的細長遮罩 → `ValueError` 含 `DEGENERATE_MASK` 原文。
  - `test_composite_offcanvas`（Review Focus 5）：x 0、y 1、size 1.0、flip → 輸出尺寸 == 背景尺寸，不丟例外。
  - 角色與遮罩尺寸不同 → `ValueError` 含「遮罩與角色圖尺寸不同」。
  - 背景 2 張＋角色 1 張 → 輸出 2 張。
- [ ] **Step 2：** FAIL → 實作 → PASS；更新 registration。
- [ ] **Step 3：** Commit。

### Task 7：線稿、補線、版面、存檔

**Files:**
- Create: `nodes/lineart.py`、`nodes/page.py`、`tests/nodes/test_lineart_nodes.py`、`tests/nodes/test_page_nodes.py`

**Interfaces:**
- Consumes: `render_lineart`、`close_gaps_auto`、`layout_print_page`、`png_bytes`、`FONT_PATH`、`PaperSpec`
- Produces:
  - `CBLineArt(image: IMAGE, paper: CB_PAPER, merge_outlines=True) -> IMAGE`（黑白，`paper.layout.content_px`）
  - `CBCloseGaps(lineart: IMAGE, paper: CB_PAPER, enabled=True) -> IMAGE`（關掉時原樣通過）
  - `CBPageLayout(lineart: IMAGE, paper: CB_PAPER, text_zh: STRING="", text_en: STRING="", show_zh=True, show_en=True) -> IMAGE`（整頁 `layout.page_px`；空字串視為 None）
  - `CBSavePrintPNG(page: IMAGE, filename_prefix="coloring_book/page")`：輸出節點；用 `folder_paths.get_save_image_path` 取路徑，每張存成 1-bit PNG（`dpi=(300,300)`），沒有 `--disable-metadata` 時把 hidden `prompt`／`extra_pnginfo` 寫進 PNG text chunk（同 SaveImage 的鍵名）；回傳 UI 預覽清單（type `output`）。
- [ ] **Step 1：** 測試：
  - `composite.png` → LineArt(A4, 11, standard, merge) == `ink_a4.png`；merge False == `ink_a4_nofill.png`；Letter/detailed/14 == `ink_letter_detailed.png`。
  - CloseGaps(ink_a4) == `ink_a4_fixed.png`；enabled False → 原樣。
  - PageLayout：三種文字組合 == `page_a4_both.png`／`page_a4_blank.png`／`page_a4_none.png`。
  - `test_layout_whitespace_caption`：中文 `"  ​ \n "` 英文 `""`、兩個都開 → == `page_a4_none.png`。
  - `test_layout_long_caption`：200 字中文 → 不丟例外、輸出尺寸 == page_px。
  - 字型檔不見（monkeypatch `FONT_PATH`）→ `ValueError` 含 `CAPTION_FONT_MISSING` 原文。
  - SavePrintPNG：存出的檔案 `Image.open().mode == "1"`、`info["dpi"] ≈ (300, 300)`、尺寸 2480×3508（A4）、有 `prompt` text chunk；`disable_metadata` 時沒有。
  - registration 預期集合此時 == 12 個。
- [ ] **Step 2：** FAIL → 實作 → PASS（comfy venv）；core venv 跑 `tests/` 仍 PASS。
- [ ] **Step 3：** Commit。

### Task 8：範例 workflow、翻譯、README、授權查證

**Files:**
- Create: `tools/build_workflows.py`、`example_workflows/1_make_character.json`、`example_workflows/2_make_page.json`、`locales/zh-TW/main.json`、`README.md`、`README.zh-TW.md`、`tests/nodes/test_workflows.py`

**Interfaces:**
- Consumes: 所有節點 id 與輸入名稱；網站 `comfy_workflows/qwen_*/workflow_api.json` 的內建節點接法與固定值
- Produces: 兩個 UI 格式（ComfyUI 前端存檔格式，含 nodes、links、groups）的 workflow

- [ ] **Step 1：** 確認 ComfyUI 在跑（`curl -s :8188/system_stats`）；沒跑就請使用者啟動或同意由我用 `--cpu` 在另一個 port 臨時啟動（不碰 GPU）。存一份 `/object_info` 到 `tests/fixtures/object_info.json`（只留用到的類別）。
- [ ] **Step 2：** `tools/build_workflows.py`：以 Python 描述圖（節點類別、widget 值、連線、位置、群組），輸出 UI 格式 JSON。內容照 spec §5.1：
  - 共用載入：`UnetLoaderGGUF` → `LoraLoaderModelOnly`（1.0）→ `ModelSamplingAuraFlow`（3.1）→ `CFGNorm`（1.0）；`CLIPLoader`（type `qwen_image`）；`VAELoader`。
  - 主角：CBCharacterPrompt → TextEncodeQwenImageEditPlus（無 image）＋空字串 NEGATIVE → `FluxKontextMultiReferenceLatentMethod`（`index_timestep_zero`）×2 → `EmptySD3LatentImage` 832×1216 batch 2 → KSampler（4、1.0、euler、simple、seed `fixed`）→ VAEDecode → SaveImage `coloring_book/character`。
  - 做一頁：同上接法；背景 latent 的寬高接 CBPaper 的 width/height；動作 latent 832×1216，image1 接 CBLoadCharacter；`LoadBackgroundRemovalModel`（birefnet）→ `RemoveBackground` → CBCleanMask；上傳背景的 LoadImage 照 Task 5 Step 1 的結論設成預設不用；所有 KSampler seed `fixed`；加 Note 節點（中英）說明：換一張改 seed、上傳背景怎麼開、模型放哪。
- [ ] **Step 3：** `test_workflows.py`（core venv 也能跑，只讀 JSON）：每個節點的 type 在 `object_info.json` 或本包的 12 個 id 裡；每個 widget 值數量與 object_info 的輸入一致；每條 link 兩端型別相同；所有 KSampler 的 control_after_generate == "fixed"；模型檔名 == 網站 manifest 的檔名。
- [ ] **Step 4：** `locales/zh-TW/main.json`：12 個節點的顯示名稱、輸入名稱、下拉選項的中文（表情／面向用 actions.ts 的中文標籤）。查 ComfyUI 前端是否支援 `zh-TW` 語系（看 `~/projects/comfyui` 前端套件的 locales 清單）；不支援就同內容放 `locales/zh/`，寫進 SOURCE.md。
- [ ] **Step 5：** 授權查證：用 web-fetch 打開下列頁面的授權欄位並記錄（網址＋看到的授權）在 README 模型表：HF `Comfy-Org/Qwen-Image_ComfyUI`、`lightx2v/Qwen-Image-Edit-2511-Lightning`、`Comfy-Org/BiRefNet`、`unsloth/Qwen-Image-Edit-2511-GGUF`、GitHub `city96/ComfyUI-GGUF`。任何一項不是可再散布／可商用的開源授權 → 停下來回報使用者，不繼續 Task 9–10。
- [ ] **Step 6：** README（英文為主）＋README.zh-TW：安裝（Manager／git clone＋`pip install -r requirements.txt`）、需要 ComfyUI ≥ 0.36.0 與 ComfyUI-GGUF、模型表（檔名、HF 連結、子資料夾、授權）、兩個 workflow 怎麼用（主角重複使用、上傳背景、seed fixed）、節點清單、「與網站同一套演算法」、字型授權、GPL-3.0。截圖位置先留 TODO，Task 9 補上。
- [ ] **Step 7：** Commit。

### Task 9：GPU 驗收（**先問使用者 GPU 是否可用**）

**Files:**
- Create（網站 repo）: `experiments/results/phase15_comfy_nodepack_<YYYYMMDD_HHMM>/NOTES.md` 與輸出、截圖
- Modify（節點包）: README 截圖、`example_workflows/*.jpg` 縮圖

- [ ] **Step 1：** 問使用者 GPU 是否空著；得到同意才繼續。`~/tools/comfy-cli-venv` 安裝 `requirements.txt`。以額外的 `extra_model_paths` 設定（`custom_nodes:` 指向一個只含節點包 symlink 的 scratch 資料夾）啟動 ComfyUI，不改 `~/projects/comfyui`。若 8188 已有本專案的 ComfyUI 在跑，先問使用者能否重啟。
- [ ] **Step 2：** 用瀏覽器（Playwright）開 ComfyUI，從範本瀏覽器載入兩個 workflow，跑：① 產生主角 ② 做一頁（生成背景）③ 做一頁（上傳背景：確認背景的 KSampler 沒有執行——看執行紀錄／`/history` 的節點輸出）④ 只改英文字重跑：確認只有 CBPageLayout、CBSavePrintPNG 重算（`/history` 的 cached 節點）。每次記耗時、截圖、輸出 PNG。
- [ ] **Step 3：** 對照：把 ② 的 Composite 輸出與 CBPageLayout 輸出，和網站模組用同一組 bg/pose/mask 算出的結果比，記錄差異像素數（comfy venv 的 Pillow 版本不同，只記錄）。
- [ ] **Step 4：** 安裝驗收：另一個空的 custom_nodes 資料夾，`git clone` 節點包（本機路徑）＋`pip install -r requirements.txt`，照 README 從零跑 ②。
- [ ] **Step 5：** 介面切到繁體中文確認翻譯有出現。截圖放進 README 與 `example_workflows/*.jpg`。
- [ ] **Step 6：** NOTES.md：每項結果、耗時、VRAM 峰值、差異像素數、遇到的問題。兩個 repo 各自 commit。停下來，把結果與截圖給使用者看。

### Task 10：上架（**每一步都先問使用者**）

- [ ] **Step 1：** 問使用者：GitHub 帳號／組織、repo 名稱（預設 `ComfyUI-ColoringBook`）、是否由我用 `gh` 建立公開 repo。
- [ ] **Step 2：** 使用者在 registry.comfy.org 建立 publisher、產生 API key、存成 repo secret `REGISTRY_ACCESS_TOKEN`。把 PublisherId 填進 `pyproject.toml` 的 `[tool.comfy]`，`Repository` 填 GitHub 網址。
- [ ] **Step 3：** 新增 `.github/workflows/publish.yml`（Comfy-Org 的 publish action，`pyproject.toml` 變更時觸發）。`comfy node validate`（若 comfy-cli 有這個指令）通過。
- [ ] **Step 4：** 使用者同意後推送；Action 發佈成功後，用 ComfyUI-Manager 搜尋並安裝一次，跑 ②，截圖。
- [ ] **Step 5：** 網站 repo：更新 CLAUDE.md 的 Phase 紀錄一行、merge `phase15-comfy-nodepack`（使用者同意後）。
