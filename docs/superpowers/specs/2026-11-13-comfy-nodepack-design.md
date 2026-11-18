# Phase 15：單頁著色頁 ComfyUI 節點包＋workflow 上架 — 設計

日期：2026-11-13　狀態：草稿（等使用者確認）
示意圖與決策頁：https://claude.ai/artifact/EkW4oM6PopUzB7RzynstYh

## 1. 目的

把網站「做一頁」的流程做成 ComfyUI 節點包，附兩個 workflow，上架讓 ComfyUI 使用者自己在本機跑：

- **產生主角**：主角描述 → Qwen 文字生圖 → 主角 PNG。
- **做一頁**：主角圖（上次產生的或自己上傳的）＋場景＋動作＋中英文字 → 背景、角色動作去背 → 合成 →
  線稿（含自動合併外框）→ 加文字、排成 A4 → 1-bit 300 DPI PNG，可以直接列印著色。

主角圖存成 PNG，可以重複使用，也可以換成任何上傳的圖。

### 目標
- 同一張合成圖，節點包和網站做出的線稿、整頁 PNG **逐像素相同**（同一份演算法、同一組參數）。
  這只在固定套件版本的測試環境保證（§7）；使用者的 ComfyUI 裝的 Pillow 等版本可能不同，README 只承諾同一套演算法。
- 在 16 GB 顯卡（Q4_K_M GGUF）跑得動；ComfyUI-Manager 搜得到，一鍵安裝。
- 改文字、位置、線寬、線條細節後重跑，**不重新生成**（ComfyUI 快取：生成節點的 seed 固定）。
  ComfyUI 的快取鍵包含所有上游節點的 widget 值（`comfy_execution/caching.py` get_node_signature），所以線寬、線條細節
  放在 CB Line Art，不放在接到背景 latent 的 CB Paper。改紙張會重新生成背景（seed 相同就是同一張圖；
  生成尺寸依 `compute_layout`，A4／A5／B5 相同）。

### 非目標
- 印刷廠版（出血、裁切框、安全區）。
- PDF、多頁成書、書本管理、帳號（網站的功能，不搬）。
- 在 ComfyUI 畫面上拖曳角色位置（要寫前端 JS，之後再說）。
- 手動修線（網站進階編輯的畫筆／橡皮擦）。
- 雲端、付費、模型下載器；節點包不附任何模型。
- 改動網站：網站程式、測試、資料庫都不動。

### 反目標（做到了也算失敗）
- 節點包裡偷偷下載模型或連網。
- 線稿與網站結果不一致，卻沒有人發現（所以要有對照測試，§7）。
- 預設 workflow 每改一個字就重跑 GPU。

## 2. 已決定事項（使用者 2026-11-13）

| 項目 | 決定 |
|---|---|
| 上架平台 | Comfy Registry＋公開 GitHub repo（OpenArt／Civitai 之後再說） |
| 節點切法 | 中：每一步一個節點，可以預覽、可以換掉（§4） |
| PDF | 第一版不做 |
| 授權 | GPL-3.0；使用者確認程式可以公開 |
| 程式共用 | 把需要的純影像程式**複製**到新 repo；網站不動；記下來源 commit |
| Repo 位置 | 獨立 git repo：`/ai-data/repos/ComfyUI-ColoringBook` |
| 主角載入 | 新增「載入主角」節點：列出產生過的主角與上傳的圖，透明部分鋪白底 |
| 驗收環境 | 可以把 scikit-image（與缺的 opencv-headless）裝進 `~/tools/comfy-cli-venv` |
| 列印方式 | 只做家用版（四邊 1 公分白邊，`PrintMode.HOME`）；不做印刷廠出血／安全區 |
| 角色位置 | 數字欄位（水平、垂直、大小、左右翻轉），預設同網站 0.5／0.66／0.6／不翻 |
| 上傳背景 | 「背景來源」切換節點：接了上傳圖就用它，並跳過背景生成 |

## 3. 方案比較（程式怎麼共用）

| 方案 | 好處 | 代價 | |
|---|---|---|---|
| A. 複製到新 repo | 網站完全不動；節點包獨立發版 | 網站之後改線稿要手動同步 | **選這個** |
| B. 抽成共用套件 | 只有一份程式 | 這階段就要改網站 import 與 1,450 個測試的匯入路徑；範圍變大 | |
| C. 節點包直接 import 網站後端 | 不用複製 | 使用者要裝整個網站；不可能上架 | 排除 |

同步方法：新 repo 的 `coloring_core/SOURCE.md` 記錄來源 commit 與每個檔案對應的網站檔案；對照測試（§7）
用網站的固定測試圖產生的參考輸出，網站改了演算法時這個測試會提醒要同步。

## 4. 節點

全部以 ComfyUI 的 V3 節點 API（`comfy_api.latest`，ComfyUI 內建的 BiRefNet 節點也用它）撰寫。
分類 `Coloring Book`，節點 id 前綴 `CB`。名稱英文，另附繁中翻譯（§5.3）。

| # | 節點 | 輸入 | 輸出 | 來源（網站檔案） |
|---|---|---|---|---|
| 1 | **CB Character Prompt** 主角提示詞 | 描述（多行文字）、變化語（下拉：無＋網站 4 個） | STRING | `prompts.qwen_character_prompt`、`QWEN_CHARACTER_VARIATIONS` |
| 2 | **CB Background Prompt** 背景提示詞 | 場景（多行文字）、變化語（下拉：無＋4 個構圖） | STRING | `prompts.qwen_background_prompt` |
| 3 | **CB Pose Prompt** 動作提示詞 | 動作（多行文字）、表情（下拉：無＋8）、面向（下拉：無＋3）、變化語（下拉：無＋4） | STRING | `prompts.qwen_pose_prompt`；表情／面向片語來自 `frontend/src/lib/actions.ts` |
| 4 | **CB Background Switch** 背景來源 | 生成的背景（IMAGE，lazy）、上傳的背景（IMAGE，選填，lazy）、紙張（CB_PAPER） | IMAGE | `uploads` 的 cover_crop；lazy 判斷是新的 |
| 5 | **CB Clean Mask** 遮罩清理 | MASK | MASK | `compose.largest_component`（最大一塊、補洞、保留柔邊） |
| 6 | **CB Composite** 合成 | 背景 IMAGE、角色 IMAGE、MASK、水平 0–1、垂直 0–1、大小 0.1–1、翻轉 | IMAGE | `compose.compose`、`Placement` 驗證 |
| 7 | **CB Paper** 紙張 | 紙張（A4／A5／B5／Letter／正方形 21 cm）——只有這一個 widget（它的寬高接到背景 latent，多的 widget 會讓背景重新生成，§1） | CB_PAPER、背景生成寬、高（INT） | `layout.compute_layout` |
| 8 | **CB Line Art** 線稿 | 彩色 IMAGE、CB_PAPER、線寬 8–16（預設 11）、線條細節（簡單／標準／細緻，預設標準）、自動合併外框（布林，預設開） | IMAGE（黑白，內容區尺寸）、線寬（INT，給 CB Close Gaps） | `lineart_render.render_lineart`（xdog → `fill_outlines` → `lineart_post.render_ink`）、`lineart_render` 常數 |
| 9 | **CB Close Gaps** 自動補線 | 線稿 IMAGE、線寬（INT，只能接線，來自 CB Line Art，兩處不會填出不同線寬）、開關（預設開） | IMAGE | `qa.evaluate`＋`autofix.plan_autofix`（沒有 user 層）＋`apply_strokes` |
| 10 | **CB Page Layout** 文字與版面 | 線稿 IMAGE、CB_PAPER、中文、英文、印中文、印英文 | IMAGE（整頁） | `export.layout_print_page`、`captions.render_caption_page` |
| 11 | **CB Save Print PNG** 存成列印 PNG | 整頁 IMAGE、檔名前綴 | （輸出節點，畫面上預覽） | `export.png_bytes`：1-bit、dpi 300 |
| 12 | **CB Load Character** 載入主角 | 下拉：產生過的主角（值 `coloring_book/character_00001_.png [output]`）＋`input/` 的圖（裸檔名），格式同 Load Image 才有縮圖；附上傳按鈕與重新整理 | IMAGE | 新的；透明部分用 `imageio.flatten_on_white` 鋪白底（ComfyUI 的 Load Image 會直接丟掉 alpha，透明處變黑） |

說明：
- 示意圖寫「約 9 個」；多出 **CB Load Character**（使用者 2026-11-13 決定）與 **CB Paper** 是因為線稿（統一線寬要用最終尺寸）、版面、背景裁切與生成尺寸都要同一份紙張設定，
  獨立一顆比每個節點各填一次不容易填錯。線寬只在 CB Line Art 填一次，再由它的輸出接到 CB Close Gaps。
- 生成部分不寫節點：全部用 ComfyUI 內建節點＋ComfyUI-GGUF 的 `UnetLoaderGGUF`，接法照網站三個 workflow 的
  `workflow_api.json`（ModelSamplingAuraFlow 3.1、CFGNorm 1.0、Lightning LoRA 1.0、euler／simple、4 步、cfg 1、
  `FluxKontextMultiReferenceLatentMethod index_timestep_zero`、`LoadBackgroundRemovalModel`＋`RemoveBackground`）。
- IMAGE／MASK 是 ComfyUI 的 torch 張量（`[B,H,W,C]`、`[B,H,W]`、0–1 浮點）；節點內轉成 numpy uint8 交給複製過來的函式，
  再轉回張量。黑白 IMAGE 的值只有 0 和 1。
- 批次：每個節點逐張處理；某個輸入只有 1 張時套用到全部。不同批次大小（都 > 1 且不相等）時報清楚的錯誤。
- 錯誤訊息沿用網站的中文訊息（例如「角色動作的去背結果太細長，請重新產生角色動作」），英文介面也顯示同一句，
  前面加英文短句。
- 提示詞節點的變化語預設「無」：在 ComfyUI 裡換 seed 就會換一張，不需要網站那種「不重複上一張」的隨機挑選。
- 字型：Noto Sans TC 可變字型（12 MB）放在 `fonts/`，附 `OFL.txt`。

## 5. Workflow 與套件

### 5.1 兩個 workflow（放在 `example_workflows/`，ComfyUI 的範本瀏覽器會列出來）

**1_make_character.json 產生主角**
描述 → CB Character Prompt → Qwen 文字生圖（832×1216，batch 2）→ SaveImage（前綴 `coloring_book/character`）。

**2_make_page.json 做一頁**
```
CB Load Character（主角）─────┐
CB Pose Prompt ──→ Qwen 編輯（image1＝主角）→ RemoveBackground ─→ 角色 IMAGE ─┐
                                                └→ MASK → CB Clean Mask ─────┤
CB Background Prompt → Qwen 文字生圖 ─→ CB Background Switch ←─ LoadImage（上傳背景，預設靜音）
                                                 │                            │
                                                 └──────→ CB Composite ←──────┘
                                                              ├──→ 預覽
CB Paper（只有紙張）──→ CB Line Art（線寬、線條細節）←── 合成圖 ┘
       │                   └─ 線稿＋線寬 ──→ CB Close Gaps
       └──→ CB Page Layout ← 補線後線稿＋中英文字 → CB Save Print PNG
（CB Paper 的寬、高另外接到背景的 EmptySD3LatentImage）
```
- 三組生成共用同一組載入節點（UNET、CLIP、VAE、LoRA），模型只載入一次。
- 背景與角色動作的 KSampler seed 預設 **fixed**：改文字、位置、線寬、線條細節重跑時只重算後面的影像處理（幾秒）；
  改紙張會重新生成背景（seed 相同就是同一張圖）。想換一張就改 seed。workflow 裡用 Note 節點寫明這件事。
- 背景生成尺寸由 CB Paper 的寬、高輸出決定（`compute_layout` 的 generation_size：A4／A5／B5 832×1216；
  Letter 896×1152；正方形 1024×1024），連到 EmptySD3LatentImage；上傳的背景也 cover_crop 到這個尺寸。
- 角色動作固定 832×1216（同網站）。
- 主角圖不另外縮放（Qwen 的 image1 會自己縮放；網站的 letterbox 只是為了儲存一致）。透明背景由 CB Load Character 鋪白（§4）。

### 5.2 上傳背景與跳過生成
CB Background Switch 的兩個輸入都是 lazy：有上傳的背景就只要求上傳那一路，背景生成那一串就不會執行。
上傳的背景依紙張比例 cover_crop（等比例縮放到蓋滿再置中裁切）。

待實作時用 ComfyUI 0.36 驗證：上傳背景的 LoadImage 在「不想用上傳」時要怎麼關掉（略過 bypass、靜音 mute 或拔掉連線），
下游收到的是什麼。如果三種都不好用，改成在 CB Background Switch 加一個「使用上傳的背景」開關，
並附一個允許空白的載入節點。選定的做法寫進 workflow 的 Note。

### 5.3 Repo 結構
```
ComfyUI-ColoringBook/
  __init__.py            # ComfyExtension，註冊節點
  nodes/                 # 節點（張量 ↔ numpy 轉換、輸入驗證）
  coloring_core/         # 從網站複製的純影像程式（numpy／opencv／Pillow／scikit-image）
    SOURCE.md            # 來源 commit 與檔案對照
  fonts/NotoSansTC-VF.ttf, fonts/OFL.txt
  example_workflows/1_make_character.json, 2_make_page.json（＋同名 .jpg 縮圖）
  locales/zh-TW/main.json（必要）、locales/zh/main.json（若 ComfyUI 前端沒有 zh-TW 才改用）
  tests/                 # pytest，不需要 GPU、不需要 ComfyUI 在跑
  pyproject.toml         # [project] + [tool.comfy]（PublisherId、DisplayName、Icon）
  requirements.txt       # opencv-python-headless、scikit-image（numpy、Pillow、torch 由 ComfyUI 提供）
  README.md（英文）、README.zh-TW.md、LICENSE（GPL-3.0）
  .github/workflows/publish.yml   # pyproject 版本變更時發佈到 Registry
```
- Registry 名稱 `comfyui-coloringbook`（小寫），顯示名稱「Coloring Book Page Maker」（我的判斷，使用者可改）。
- 最低 ComfyUI 版本寫實際驗收的 **0.36.0**（`requires-comfyui`）。BiRefNet 內建節點 0.21.0 就有，但 V3 節點 API 的 lazy 輸入
  在舊版沒驗證過，不宣稱支援。
- 相依的自訂節點：ComfyUI-GGUF（README 說明；ComfyUI-Manager 安裝 workflow 缺的節點時會提示）。

## 6. 模型與授權

README 列出模型、下載連結與放置的子資料夾（同網站 manifest）：

| 模型 | 資料夾 | 授權 | 查證狀態 |
|---|---|---|---|
| qwen-image-edit-2511-Q4_K_M.gguf（unsloth） | `unet` | Apache-2.0（沿用 Qwen-Image-Edit-2511） | 原模型 HF 模型卡已確認 |
| qwen_2.5_vl_7b_fp8_scaled.safetensors（Comfy-Org） | `text_encoders` | Apache-2.0 | 實作時打開 HF 頁確認 |
| qwen_image_vae.safetensors（Comfy-Org） | `vae` | Apache-2.0 | 實作時確認 |
| Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16（lightx2v） | `loras` | Apache-2.0 | 只有第三方列表，實作時確認 |
| birefnet.safetensors（Comfy-Org） | `background_removal` | MIT | ComfyUI 官方文件寫 MIT，實作時確認 |

其他：Noto Sans TC（OFL 1.1，可內附，要附 OFL.txt）、ComfyUI（GPL-3.0）、ComfyUI-GGUF（Apache-2.0，使用者自己裝）。
任何一項查到不能再散布或不能商用，停下來回報，不上架。

## 7. 測試與驗收

- **單元測試**（pytest，CPU）：複製網站對應的測試（compose、lineart、captions、layout、qa、autofix）；
  節點層測試直接呼叫節點的 execute（張量進、張量出）、批次與錯誤訊息、lazy 判斷。
  ComfyUI 的 `comfy_api` 匯入在測試時用 stub 或把 ComfyUI 放進 PYTHONPATH（實作時選一種，測試不能需要 GPU）。
- **測試環境**：`requirements-dev.txt` 固定和網站相同的 numpy／opencv／Pillow／scikit-image 版本（網站 venv：Pillow 13.0.0、numpy 2.5.4、
  scikit-image 0.26.0；實作時以 `pip freeze` 為準）。`requirements.txt` 給使用者，只寫下限，不鎖版本。
- **對照測試**：用網站產生同一批參考輸出（固定的合成圖、遮罩、上傳背景 → 線稿、補線後、整頁 PNG），存成測試資料；
  在測試環境中節點包的輸出必須逐像素相同。GPU 驗收時另外在 ComfyUI 的 venv 跑一次，記錄差異像素數（只記錄，不擋）。產生參考輸出的腳本放在網站 repo 的 `scripts/`（只讀網站程式，不改它）。
- **節點驗證**：所有用到的內建節點名稱與輸入欄位，用執行中 ComfyUI 的 `/object_info` 確認（CLAUDE.md 規則）。
- **GPU 驗收**（等使用者說 GPU 可用；使用者同意把 scikit-image 等缺的套件 pip 安裝到 `~/tools/comfy-cli-venv`）：在本機 ComfyUI 載入節點包——用啟動參數另外指定 custom_nodes 資料夾，
  不改 `~/projects/comfyui` 的安裝——跑兩個範例 workflow：
  ① 產生主角 ② 做一頁（生成背景）③ 做一頁（上傳背景，確認背景生成沒跑）④ 只改文字重跑，確認沒有重新生成。
  記錄耗時、截圖、輸出，寫在 `experiments/results/phase15_comfy_nodepack_<日期>/`。
- **安裝驗收**：在全新的 ComfyUI 資料夾（venv 共用 `~/tools/comfy-cli-venv`）用 `git clone` 安裝，照 README 從零跑一次。
  Registry 發佈後再用 ComfyUI-Manager 安裝一次。

## 8. 上架步驟（實作最後，需要使用者）

1. 使用者提供 GitHub 帳號／組織名稱、建立公開 repo（或授權我用 `gh` 建立）。
2. 使用者在 registry.comfy.org 建立 publisher（PublisherId 之後不能改）、產生 API key，存成 repo secret `REGISTRY_ACCESS_TOKEN`。
3. 推送、打 tag，GitHub Action 發佈到 Registry。
4. 推送與發佈都是對外動作，每一步都先問使用者。

## 9. 留給實作者決定的事
- 節點內部的張量轉換、批次處理的寫法、測試怎麼 stub `comfy_api`。
- 範例 workflow 的版面配置、群組（group）或子圖（subgraph）的使用、縮圖。
- 下拉選單的顯示文字（英文＋中文括號，或靠翻譯檔）。
- README 的章節與截圖。

## 10. 設計時我做的判斷（請確認）
- 多一顆 CB Paper，紙張設定只填一次；它也輸出背景生成尺寸。
- 生成節點的 seed 預設 fixed，換圖改 seed。
- 提示詞節點的變化語預設「無」，不做網站「不重複上一張」的隨機。
- 主角圖不 letterbox，只鋪白底。
- 逐像素相同只在固定版本的測試環境保證；使用者環境不鎖套件版本。
- 最低 ComfyUI 版本寫 0.36.0。
- Registry 名稱 `comfyui-coloringbook`、顯示名稱「Coloring Book Page Maker」。
- 自動補線、自動合併外框預設開。
