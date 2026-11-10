# Qwen-Image-Edit-2511 畫背景（免換模型）spike（2026-11-10）

問題：精準動作用 Qwen-Image-Edit-2511、背景用 SDXL，兩組模型塞不進 31 GB RAM，每次「背景 → 動作」都要換模型
（phase6_qwen_pose_ui_check_20261110_0257：背景 6 s → 18–27 s、每頁約 75 s）。
如果背景也改用 Qwen，線稿品質能不能至少跟 SDXL 一樣好？速度能省多少？

結論先講：**品質大勝（缺口 53 → 9.5、過小區域 208 → 82、目視 1.9 → 4.3），速度只小幅改善（互動流程每頁約 57 s → 45 s）。建議採用 Qwen 背景（理由是品質，不是速度）。**

## 設定

- ComfyUI 0.36.0 @ 8188、RTX 5080 16 GB、RAM 31 GB。每批開始前確認 GPU 閒置（util ≤10%、/queue 空）；ComfyUI history 確認期間沒有其他人的工作插隊。
- 尺寸：A4「印刷店」（新書預設）的 `generation_size` 864×1216；線稿指標沿用 phase5／phase6：
  `render_base(img, (2244, 3272), STANDARD, 11)` → `qa.evaluate`，記 `gap_count`、`tiny_region_count`。
- 場景 6 個 × seed 101、202。park／kitchen／bedroom 沿用 phase5_background_prompt_20261109 的場景文字，另加 beach、classroom、forest。
- 腳本 `run.py`；逐張數據 `results.csv`；圖、線稿、總覽、`metrics.json`、`swap.json` 在 `processed/`（不進版控）。
  - `processed/sheet.jpg`：列＝場景；欄＝SDXL s101、SDXL s202、QWEN s101、QWEN s202（QWEN＝採用的 QA1）。
  - `processed/sheet_lineart.jpg`：同上的 A4 線稿；`sheet_QB1.jpg`／`sheet_lineart_QB1.jpg`：選項 b。

### SDXL 基準（app 完全相同的參數）

正式 `sdxl_background` workflow 經 `WorkflowRegistry.build_prompt`：`prompts.background_prompt(scene)`（C_weighted）＋
`BACKGROUND_NEGATIVE`、全白結構圖、`controlnet_strength 0`、scribble、end 0.5、manifest 預設 30 steps／cfg 6／dpmpp_2m karras。

### Qwen 背景圖（`run.py` 的 `qwen_bg_graph`）

由 `qwen_edit_pose_cutout` 拿掉 CHARACTER_REF 與 BiRefNet 三個節點。所有節點與欄位皆以 /object_info 驗證：
`TextEncodeQwenImageEditPlus` 只有 `clip`、`prompt` 必填，`vae`、`image1`～`image3` 皆為 optional。

| title | class_type | 參數 |
|---|---|---|
| UNET | UnetLoaderGGUF | qwen-image-edit-2511-Q4_K_M.gguf |
| TEXT_ENCODER | CLIPLoader | qwen_2.5_vl_7b_fp8_scaled.safetensors, type qwen_image |
| VAE | VAELoader | qwen_image_vae.safetensors |
| MODEL_SAMPLING | ModelSamplingAuraFlow | shift 3.1 |
| CFG_NORM | CFGNorm | 1.0 |
| LIGHTNING_LORA | LoraLoaderModelOnly | Lightning 4-step V1.0 bf16, 1.0 |
| POSITIVE／NEGATIVE | TextEncodeQwenImageEditPlus | clip＋vae；NEGATIVE 為空字串 |
| LATENT | EmptySD3LatentImage | 864×1216 |
| SAMPLER | KSampler | 4 steps、cfg 1、euler／simple、denoise 1 |
| DECODE → OUTPUT | VAEDecode → SaveImage | |
| （僅選項 b）BLANK_PAGE | EmptyImage | 864×1216、color 0xFFFFFF，接 POSITIVE／NEGATIVE 的 image1 |
| （僅選項 b）REF_METHOD_POS／NEG | FluxKontextMultiReferenceLatentMethod | index_timestep_zero |

兩種寫法（cfg 1 時負向無效，排除條件寫進正向）：

- **QA1（選項 a，不接 image1，描述式）**：
  `{scene}. Simple coloring book background, a few large simple objects, lots of empty space, children's picture book illustration, flat colors, thick clean black outlines, simple shapes. Empty scene, no people, no animals, no characters, no text.`
- **QB1（選項 b，全白 image1，指令式）**：
  `Draw on this blank page: {scene}. Simple coloring book background, … simple shapes. Fill the whole page with the scene. No people, no animals, no characters, no text.`

兩個選項都能直接出圖（先用 park、kitchen 試跑確認）。

## 結果（每列 12 張）

| 版本 | 平均缺口 | 平均過小區域 | 區域數 | 黑色比例 | 有人／動物 | 目視（1–5） | 熱機秒數：新場景 | 熱機秒數：同提示詞 |
|---|---|---|---|---|---|---|---|---|
| SDXL（app 現行） | 52.9 | 207.9 | 348 | 0.178 | 0/12 | 1.9 | 6.0 | 6.4 |
| **QA1（Qwen，無 image1）** | **9.5** | **82.3** | 142 | 0.090 | 0/12 | **4.3** | 18.5 | 12.5 |
| QB1（Qwen，白圖 image1） | 7.8 | 61.9 | 126 | 0.081 | 0/12 | 3.9 | 24.9 | 16.1 |

- 熱機：每個家族先跑一張不計時的暖機圖（不同 seed），之後同家族連跑 12 張。
- 「新場景」＝提示詞第一次出現（s101，扣掉 park：park 與暖機同提示詞）；「同提示詞」＝s202，文字編碼被 ComfyUI 快取，
  相當於 app 的「再產生一張」。Qwen 的文字編碼器（Qwen2.5-VL 7B，約 7.9 GB）每次都要搬進 VRAM，佔約 6 s。
- 各場景（缺口／過小區域，s101、s202）：

| 場景 | SDXL | QA1 | QB1 |
|---|---|---|---|
| park | 30/363、66/296 | 16/81、10/74 | 18/95、9/111 |
| kitchen | 18/172、42/113 | 7/66、9/88 | 7/60、8/43 |
| bedroom | 44/84、52/97 | 6/70、15/73 | 5/70、4/57 |
| beach | 55/97、53/92 | 6/65、9/58 | 5/33、6/18 |
| classroom | 30/132、23/216 | 7/91、2/73 | 3/78、12/99 |
| forest | 125/301、97/532 | 16/106、11/143 | 9/39、8/40 |

## 換模型成本（實測）

起點：先跑一張 Qwen 動作（模擬上一頁剛做完精準動作）。每個序列 4 步、每步換 seed（避免快取）。
背景場景 beach、forest；動作 `qwen_pose_prompt("waving one hand")`、參考圖 `p5acc/storage/books/1/generations/1.png`、832×1216。
第一輪（rep0、rep1）先跑 qwen_only，第二輪（rep2、rep3）先跑 alternating，抵銷順序偏差。

| 序列 | rep0 | rep1 | rep2 | rep3 | 平均（rep1–3） | 背景每張 | 動作每張 |
|---|---|---|---|---|---|---|---|
| 交替：SDXL 背景 → Qwen 動作 ×2 | 127.3 | 103.8 | 122.2 | 113.7 | **113.2 s（每頁 57 s）** | 23.3 s | 33.2 s |
| 只用 Qwen：Qwen 背景 → Qwen 動作 ×2 | 128.7* | 95.9 | 85.9 | 89.8 | **90.5 s（每頁 45 s）** | 20.6 s | 24.6 s |

\* rep0 緊接在第一次換回 Qwen 之後，第一張背景 44 s，視為暖機，不列入平均（四輪全算：116.8 vs 100.1 s）。

逐步數據見 `processed/swap.json`。ComfyUI log 顯示原因：
- 交替時，SDXL 背景本身只要 6 s，但前面要把 SDXL（約 5 GB＋CLIP 1.5 GB）搬回 VRAM，變 12–31 s；接著 Qwen 動作要重新載入 GGUF UNet（12.7 GB）與文字編碼器，變 30–40 s。
- 只用 Qwen 時也不是零成本：16 GB VRAM 放不下 UNet（12.7 GB）＋文字編碼器（7.9 GB），每個新提示詞都要把 UNet 部分卸載再搬回，所以 Qwen 背景 16–31 s、動作約 25 s。
- 每頁省約 12 s（約 20%），不是原本預期的「省掉 20 s 換模型時間」。

## 觀察

1. **品質是最大差異。** SDXL 背景即使用了 C_weighted 提示詞，仍有大片灰階陰影、樹叢花叢細碎、森林滿版（缺口 97–125）；Qwen 每張都是 2–4 個大物件＋大面積留白，線條粗而封閉，幾乎就是著色本的樣子。缺口少 80%、過小區域少 60%。
2. **36 張都沒有人或動物**（三個版本皆然）；QB1 classroom s202 黑板上出現亂碼文字（「no text」沒完全擋住）。
3. **選項 a 優於 b。** 選項 b（白圖 image1）指標略好，但：要多編碼一張參考圖，慢約 6 s；畫面在「彩色」與「純線稿」之間不穩定（白頁傾向保留白色），太陽等淡色物件在線稿中會消失；出現文字。選項 a 一律是平塗彩圖，正好符合 Route C「彩圖 → xdog 擷取線稿」的假設，也能直接當網站上的彩色預覽。
4. **兩個 seed 幾乎同構圖**（與 phase6 精準動作相同的特性）：Lightning 4-step＋cfg 1 的多樣性很低，「再產生一張」大概只會換細節。若採用，需要在提示詞加隨機變化（例如視角、物件位置、時間）讓重新產生有意義。
5. **單張熱機時間 Qwen 比 SDXL 慢 3 倍**（18.5 vs 6.0 s）。如果改成「先批次產生全部背景、再批次產生全部動作」（repo 已有 model-grouped queue 的設計文件），SDXL 背景每頁約 6 s＋精準動作約 20 s，速度上 SDXL 反而較快；Qwen 背景的價值在品質，不在速度。
6. 草圖構圖（ControlNet scribble）目前只有 SDXL 有。Qwen 可試「把草圖當 image1 + Draw on this sketch …」，本次未測。

## 建議

**採用 Qwen（QA1）做為「沒有草圖時」的預設背景**，理由是線稿品質大幅提升（自動補線負擔小、孩子好著色），每頁也省約 12 s；有草圖時仍走 SDXL＋ControlNet（直到 Qwen 草圖模式驗證過）。

整合需要：
1. 新 workflow 資料夾 `comfy_workflows/qwen_background/`（本次 `qwen_bg_graph` 的節點；manifest 參數 prompt、seed、width、height、steps、cfg、lora_strength；models 與 qwen_edit_pose_cutout 相同，不含 BiRefNet）。preflight 自動涵蓋。
2. `prompts.py` 加 `qwen_background_prompt(scene)`（QA1 樣板）＋背景用的隨機變化清單（另做一次小校正，確認變化不破壞簡潔度）。
3. `comfy_engine.py`：BACKGROUND 依「有無草圖」選 workflow；Qwen 路徑沒有 negative_prompt／structure 參數。generation 紀錄照常保存 workflow 名稱與 hash。
4. 測試：manifest 驗證、engine 路由（mock ComfyUI）、提示詞樣板。
5. 若同時做 model-grouped queue：全 Qwen 時同一族群內不再需要依模型分組（只剩草圖背景會用到 SDXL）。
