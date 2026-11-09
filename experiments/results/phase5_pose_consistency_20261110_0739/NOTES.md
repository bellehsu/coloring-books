# 角色動作一致性比較（2026-11-10）

目的：使用者回報合成流程的「角色動作」步驟（`sdxl_pose_cutout`）會走樣——圍巾跑到頭上、耳朵變形，
「揮手」「雙手拿東西」等動作也不明顯。只用本機 `/ai-data/models/comfy/` 已有的模型，比較三種做法。

設定：ComfyUI 0.36.0 @ 8188（本專案的 ComfyUI）；RTX 5080 16 GB；系統 RAM 31 GB。
參考圖＝`p5acc` 驗收的 book 1 角色（`sdxl_text2img` 產生，832×1216 白底），
描述 `a small round white bunny with long ears, wearing a red scarf`（book 1 的 `character_description`）。
6 個動作＝App 預設片語；seed 101、202；共 3 候選 × 6 × 2 = 36 張（另加 OPENPOSE 強度 0.6／0.8 兩組 24 張）。
腳本：`run.py`（拋棄式）；圖在 `processed/`（不進版控）；逐張數據與評分在 `results.csv`。
總覽：`processed/sheet_combined.jpg`（列＝動作，欄＝BASELINE｜QWEN｜OPENPOSE，s101，左上角為參考圖），
各候選 `processed/sheet_<候選>.jpg`（欄＝s101、s202；OPENPOSE 多一欄骨架圖）。

## 三個候選（節點與輸入皆先查 `/object_info` 驗證）

| 候選 | 模型 | 節點／參數 |
|---|---|---|
| BASELINE | `sd_xl_base_1.0` + `ip-adapter-plus_sdxl_vit-h` + CLIP-ViT-H | 直接用 `comfy_workflows/sdxl_pose_cutout/workflow_api.json`；提示詞＝`pose_prompt(描述, 動作)`、負向＝`POSE_NEGATIVE`；IP 0.55／end 0.6／linear；832×1216；30 步 cfg 6 dpmpp_2m karras（與 `comfy_engine` 傳入的值相同） |
| QWEN | `unet/qwen-image-edit-2511-Q4_K_M.gguf`（`UnetLoaderGGUF`）+ `qwen_2.5_vl_7b_fp8_scaled`（`CLIPLoader` type `qwen_image`）+ `qwen_image_vae` + `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16`（`LoraLoaderModelOnly` 1.0） | 照內建範本 `image_qwen_image_edit_2511` 的子圖：UNET → `ModelSamplingAuraFlow` shift 3.1 → `CFGNorm` 1.0 → LoRA；`TextEncodeQwenImageEditPlus`（clip、vae、image1＝參考圖）正負各一（負向空字串）→ `FluxKontextMultiReferenceLatentMethod` `index_timestep_zero`；`EmptySD3LatentImage` 832×1216；KSampler 4 步 cfg 1 euler simple。提示詞：`Make this exact character {action}. Keep the same character design, proportions, colors, scarf and ears unchanged. Full body, centered, plain pure white background, children's picture book illustration, flat colors, thick clean black outlines.` |
| OPENPOSE | BASELINE 的模型 + `controlnet-union-sdxl-1.0-promax` | BASELINE 改 IP 0.8／end 1.0，加 `ControlNetLoader` → `SetUnionControlNetType`（type `openpose`）→ `ControlNetApplyAdvanced`（strength 0.7、0→1.0、vae）；骨架用 PIL 自畫 COCO-18（黑底、controlnet_aux `draw_bodypose` 配色、肢體 0.6 亮度），矮胖大頭比例照參考圖量（兩眼距約 170 px）；提示詞與負向同 BASELINE。另跑 strength 0.6、0.8 |

三者都接同一組 BiRefNet 去背（`LoadBackgroundRemovalModel` `birefnet.safetensors` → `RemoveBackground` → `MaskToImage`）。
線稿指標：去背圖貼白底 → `lineart_render.render_base`（xdog σ1.2、A4 內容區 2244×3272、線寬 11）→ `qa.evaluate`。
遮罩「乾淨」＝最大連通塊 ≥98% 遮罩面積、≥0.5% 的塊只有一塊、面積比 0.05–0.8。
時間＝送出到拿回圖的牆鐘時間（含去背），不含上傳。

## 結果（平均，每候選 12 張）

| 候選 | 一致性 (1–5) | 動作 (1–5) | 缺口 | 過小區域 | 時間 s（平均／中位） | 遮罩乾淨 |
|---|---|---|---|---|---|---|
| BASELINE | 2.50 | 1.83 | 24.1 | 57.8 | 7.6 / 6.7 | 12/12 |
| **QWEN（GGUF）** | **4.00** | **5.00** | **15.7** | 35.3 | 21.9 / 20.5 | 9/12 |
| OPENPOSE cn 0.7 | 2.75 | 2.25 | 21.0 | 32.9 | 11.6 / 9.9 | 12/12 |
| OPENPOSE cn 0.6 | 2.75 | 2.17 | 19.3 | 39.7 | 11.8 / 10.0 | 12/12 |
| OPENPOSE cn 0.8 | 2.67 | 2.17 | 20.4 | 32.8 | 9.6 / 9.1 | 12/12 |

QWEN 時間：s101（新提示詞，要重跑文字編碼器）24–30 s（第一張含載入 35 s）；s202 沿用快取的 conditioning，15–17 s。
App 每次動作都是新提示詞，**實際每張約 25–30 s**；從 SDXL（背景）切過來時還要重新載入。
執行中 `nvidia-smi` 約 14.9 GB（ComfyUI 自動部分卸載），沒有 OOM。

## 觀察（逐張目視，見 results.csv notes）

1. **BASELINE 幾乎不照動作畫**：s101 六個動作都是同一個「雙手微張站姿」；s202 有 5/6 張多一塊紅布纏在頭上（使用者回報的「圍巾在頭上」可穩定重現），耳朵變直立。只有 sitting 有坐下。
2. **QWEN 明顯最好**：12 張動作全部到位（揮手、捧箱子／水果、跳起來、跑步側身、坐地、捧書閱讀）；
   臉、耳朵形狀與顏色、圍巾在脖子上、圍巾垂成披風都跟參考圖一致，沒有出現頭上紅布。缺點：
   - 手臂常被畫成紅色袖子（把參考圖披在手臂上的圍巾理解成外套），12 張中 10 張。
   - jumping 兩張耳朵變成直立、內側粉紅，表情換成瞇眼大笑（一致性 3 分）。
   - 有幾張加了門牙。
   - **兩個 seed 幾乎一樣**（構圖、物件位置僅小差異）→「重新產生」的變化很小。
   - 會畫地面陰影：jumping／running 的陰影與身體分離，BiRefNet 把陰影留成第二塊遮罩 → 3/12 遮罩不乾淨；
     其餘陰影貼著腳，遮罩算一塊但會一起被貼進背景。線稿上陰影變成一個橢圓圈。
   - 輸出背景是純白（SDXL 是淺灰），去背更乾淨；線稿缺口也最少。
3. **OPENPOSE 只部分有效**：耳朵往兩側垂、圍巾在脖子上這兩點比 BASELINE 像（IP 0.8／end 1.0 的效果）；
   揮手、跳躍、跑步的姿勢有跟骨架走。但：
   - 雙手拿東西只畫成雙手合在胸前，沒有物件；sitting／reading 的坐姿骨架完全沒被採用，仍是站姿，也沒有書。
   - s202 常出現第二對耳朵（4 隻耳朵）與頭上紅頭巾；骨架的人類頭部關鍵點跟兔耳衝突。
   - 身體被拉成細長（OpenPose 的人類軀幹比例），不是參考圖的矮胖。
   - 強度 0.6／0.7／0.8 差異不大，都一樣解不了坐姿與 4 耳問題。
4. 去背：SDXL 系兩組 12/12 乾淨；QWEN 只有分離陰影的問題，主體輪廓都完整。

## 失敗／未做

- **QWEN fp8（`qwen_image_edit_2511_fp8mixed.safetensors`，20.5 GB）沒有跑**：檔案大於 16 GB VRAM，必須大量卸載到系統 RAM；
  RAM 只有 31 GB，還要同時放 9.4 GB 的文字編碼器，而 ComfyUI 是使用者正在用的共用服務，
  有被系統 OOM 砍掉的風險。GGUF Q4_K_M（13.2 GB）已可穩定執行，所以採用 GGUF。
- 沒有失敗或 OOM 的生成。

## 建議

**改用 QWEN（Qwen-Image-Edit-2511 Q4_K_M GGUF + Lightning 4 步）做角色動作**：一致性 4.0 vs 2.5、動作 5.0 vs 1.8，
並消除了「圍巾到頭上」。代價是每張約 25–30 s（BASELINE 7 s）。OPENPOSE 不值得做（坐姿失效、4 耳、要維護骨架庫）。

上線前要處理：
1. 提示詞補「no ground shadow, no cast shadow」與「white furry arms, the scarf is only around the neck」（壓紅袖子），再驗一次。
2. 去背後只保留最大連通塊（或 mask 先做 largest-component），防陰影成為第二塊。
3. seed 幾乎不影響結果：「重新產生」若要有變化，需在提示詞加入隨機的小變化（例如表情、朝向），或接受變化很小。

## 整合需要的工作

- 新 workflow 資料夾 `comfy_workflows/qwen_edit_pose_cutout/`：`workflow_api.json`（即 `run.py` 的 `wf_qwen()` + `bg_nodes()`，
  節點加上 title：UNET、TEXT_ENCODER、VAE、LIGHTNING_LORA、CHARACTER_REF、POSITIVE、NEGATIVE、LATENT、SAMPLER、OUTPUT、MASK、BG_REMOVAL_MODEL）
  ＋ `manifest.yaml`（參數 prompt、character_image、seed、width/height（16 的倍數）、steps 4、cfg 1、lora_strength；
  模型：GGUF unet、qwen 文字編碼器、qwen VAE、Lightning LoRA、birefnet；custom node：ComfyUI-GGUF）。
  可沿用 `archive/comfy_workflows/route_b_qwen_edit_2511_gguf/manifest.yaml` 的模型段落（GGUF 由 `UnetLoaderGGUF` 從 `unet/` 列出，preflight 要能對到）。
- `comfy_engine.POSE_WORKFLOW` 換成新 workflow；POSE 分支不再傳 `ipadapter_weight/end_at`。
- `prompts.py` 新增 Qwen 用的指令式 `pose_prompt`（`Make this exact character {action}. ...`）；負向在 cfg 1 下無作用，可留空。
- VRAM／時間：執行中約 14.9 GB（自動部分卸載）；每張約 25–30 s。背景仍用 SDXL，31 GB RAM 放不下兩套模型的快取，
  背景 → 動作切換時會重新載入 Qwen（第一張約 35 s，視 page cache 而定）。
- 測試：manifest／參數對應用 mock 測；ComfyUI-GGUF 需在 ComfyUI 端安裝（本機已裝）。
