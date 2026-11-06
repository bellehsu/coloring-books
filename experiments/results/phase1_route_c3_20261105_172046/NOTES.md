# Phase 1 Route C 第三輪（C3，A 部分）：只畫道具的構圖草圖（2026-11-05）

- 設定：`experiments/configs/phase1_route_c3.yaml`（run.json 有完整副本）
- 生成：`route_a_sdxl_ipadapter_cn`，IP-Adapter 0.55 / 0.6，scene 背景提示詞（同 C2 的 scene），steps 30、cfg 6、832×1216。
- 姿勢／構圖：`none`（ControlNet 0）vs `props04`（scribble 0.4，前 50%）vs `props06`（0.6，前 60%）。
  草圖 `experiments/samples/pose/*_props.png`：與 C2 相同的道具與背景，但**不畫角色**（`pose_sketches.py` 的 character=False）。
- 擷取：luma、xdog、hybrid（見 `phase1_route_c3_reextract_*/NOTES.md`）。
- 對照：含火柴人的完整草圖（0.5／0.5）＝ C2 `scene_cn05`，參數與 seed 完全相同，直接引用 C2 結果。
- **81/81 格成功，無失敗**；實際生成 27 張。ComfyUI 臨時開在 127.0.0.1:8190。
- `none` 與 C2 的 `scene_none` 參數只差 structure_image（ControlNet 強度 0，不影響輸出），圖相同。

## 指標（每列 9 張：3 場景 × 3 seeds）

| 姿勢 | 擷取 | 碎片數 | 封閉區域 | 過小區域 | 黑填充 |
|---|---|---|---|---|---|
| none | luma | 98 | 153 | 84 | 0.013 |
| none | hybrid | 108 | 172 | 105 | 0.0016 |
| none | xdog | 162 | 198 | 124 | 0 |
| props04 | luma | 99 | 108 | 45 | 0.018 |
| **props04** | **hybrid** | 104 | 121 | 59 | 0.0018 |
| props04 | xdog | 98 | 151 | 81 | 0 |
| props06 | luma | 267 ± 652 | 130 | 74 | 0.040 |
| props06 | hybrid | **69** | 105 | 52 | **0.0009** |
| props06 | xdog | 99 | 131 | 71 | 0 |

props06_luma 的大數值來自 bedroom s303（整個房間是深藍夜色）：luma 碎片 2005、黑填充 0.18，hybrid 為 134 / 0.0008。

## 觀察（目視 park s202、kitchen s101、bedroom s202、bedroom s303）

1. **角色比例恢復**：只畫道具時，角色是正常的大頭身比例，不再是 C2 的蛋形身體＋細棍腿。這是本輪最重要的結果。
2. **構圖照草圖出現**：kitchen 有吊櫃、檯面、櫥櫃；bedroom 有床頭板、床、夜空窗（月亮）、檯燈；park 有鞦韆架與座板。
3. **動作部分有出來**：bedroom 兩檔都坐在床上；park props06 坐在鞦韆上，props04 只是站在座板前；
   kitchen 沒有端烤盤（烤盤屬於角色、被拿掉了）。道具位置能暗示動作，但手上的東西需要另外處理。
4. **夜景終於出現**：bedroom props 兩檔都是藍色夜晚房間、窗外有月亮星星（C1、C2 都沒做到整間房變暗）。
   這時 luma 會整片垮掉，**hybrid 能正確處理**。
5. props06 比 props04 場景更強，但角色較小，kitchen s101 props06 又出現多一隻兔子（攪拌碗的圓形被畫成兔子頭）。
6. 新問題：bedroom s303 右下角出現類似簽名的文字，負向提示詞的 signature / text 沒有完全擋住。

## 結論

- **Route C 目前最佳組合：scene 背景提示詞＋只畫道具的草圖（ControlNet scribble 0.4–0.6）＋IP-Adapter 0.55/0.6＋hybrid 擷取。**
- props04 角色最自然；props06 場景與動作較強但偶爾多出東西。兩者之間（0.5）值得下一輪確認。
- 一致性分數尚未評分：請開 `report.html` 評分、匯出 `scores.csv`，再執行 `run_matrix.py summarize`。

## 下一輪可驗證的假設

- 手上的道具（烤盤、書）：在草圖中畫在「手的位置」但不畫角色，看能否讓角色拿著。
- ControlNet 0.5／前 55% 作為預設值。
- 簽名文字：負向提示詞加權（例如 `(signature, text:1.3)`）或後處理去除角落的小型文字碎片。
