# Phase 1 第一輪：Route A（2026-10-19）

- 設定：`experiments/configs/phase1_example.yaml`（run.json 有完整副本）
- Workflow：`route_a_sdxl_ipadapter_cn` version `ad38a4b71aa1`
- 模型：sd_xl_base_1.0、ip-adapter-plus_sdxl_vit-h、CLIP-ViT-H-14、controlnet-union-sdxl-1.0-promax
- 主要參數：IP-Adapter weight 0.8 / linear / end_at 1.0；steps 30、cfg 6、dpmpp_2m karras；832×1216
- 角色參考圖：`experiments/samples/character_ref.png`（SDXL 產生的彩色範例圖）
- 矩陣：2 場景（park, kitchen）× 2 線稿方式（prompt_only, sketch_guided）× 3 seeds = 12 格，**12/12 成功，無失敗**

## 結果

| 線稿方式 | 一致性（1–5，人工） | 生成耗時 | 灰階比例 | 碎片數 | 封閉區域 | 過小區域 | 黑填充 |
|---|---|---|---|---|---|---|---|
| prompt_only | 3.33 ± 0.82 | 6.6 s（第一格含載入 32.8 s） | 0.289 ± 0.061 | 334 ± 120 | 123 ± 41 | 58 ± 30 | 0.105 ± 0.038 |
| sketch_guided | 1.00 ± 0 | 7.9 s | 0.179 ± 0.028 | 157 ± 117 | 94 ± 20 | 40 ± 23 | 0.058 ± 0.043 |

各格的數值見 `results.csv`、`summary.md`，評分見 `scores.csv`。

## 觀察

1. **輸出都是彩色圖，不是線稿。** 提示詞寫了 black and white，但 IP-Adapter（0.8、全程作用）把參考圖的藍色配色一起帶進來。
   灰階比例 0.15–0.40，乾淨線稿應接近 0。後處理只能把色塊邊緣轉成線，所以頭髮、條紋產生大量碎線
   （碎片 91–546、過小區域 20–107），不能直接當著色頁。
2. **prompt_only 的「一致性」主要來自照抄參考圖。** 6 張都是正面站姿、構圖與參考圖幾乎相同；
   kitchen 場景完全沒出現，park 只有 2/3 張出現鞦韆架。角色穩定，但場景與姿勢無法變化，不符合繪本需求。
3. **sketch_guided 的 1 分主要是測試設計問題，不能當作 ControlNet 無效的證據。**
   結構圖 `structure_sketch.png` 是程式畫的簡陋草圖（鞦韆架＋兩個橢圓），比例與角色不符，
   而且 kitchen 場景也用了同一張鞦韆圖。ControlNet 把這些梯形線條畫成罩在角色身上的形狀，造成臉部遮擋、
   身形變形，甚至出現亂碼文字（kitchen s202 底部的 "Omxdkler"）。
4. **速度**：模型載入後每張約 6–8 s，後處理約 0.5–1 s（A4 300 DPI）。

## 結論

- Route A 的管線可以穩定運作，但目前的參數設定在兩件事上都還沒達到要求：
  （a）輸出不是線稿；（b）IP-Adapter 太強，場景和姿勢都變不了。
- sketch_guided 需要換成合理的結構圖再測，本輪結果不採計。

## 下一輪要驗證的假設

- 降低 IP-Adapter 權重並提早結束（例如 weight 0.5–0.6、end_at 0.6），場景與姿勢能否變化，同時保住角色？
- 把參考圖先轉成線稿再餵給 IP-Adapter，能否避免顏色被帶進來、直接得到線稿？
- 加強負向提示詞（顏色相關詞）是否能壓低灰階比例？
