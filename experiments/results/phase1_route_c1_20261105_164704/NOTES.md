# Phase 1 Route C 第一輪（C1）：彩色生成 → 擷取線稿（2026-11-05）

- 設定：`experiments/configs/phase1_route_c1.yaml`（run.json 有完整副本）
- 生成：`route_a_sdxl_ipadapter_cn`（SDXL Base 1.0 + IP-Adapter Plus ViT-H，ControlNet 強度 0），彩色參考圖 `character_ref.png`；
  steps 30、cfg 6、dpmpp_2m karras、832×1216。
- 提示詞：`{角色}, {場景}, children's picture book illustration, flat colors, thick clean black outlines, simple shapes, plain white background`；
  負向提示詞覆寫為不含 color 的版本（見設定檔）。
- 矩陣：IP-Adapter 0.45/0.5、0.55/0.6（= C0 的 ip_weak，使用者選定的目標）、0.65/0.7 × 擷取 luma／xdog × 3 場景 × 3 seeds
  = **54 格，54/54 成功，無失敗**。實際生成 27 張（同一 IP 檔位的 luma 與 xdog 共用原圖，`gen_source` 有紀錄）。
- ComfyUI 0.36.0，臨時開在 127.0.0.1:8190（8188 被其他服務占用），以 `APP_COMFY__URL` 覆寫。

## 指標（每列 9 張：3 場景 × 3 seeds）

| IP-Adapter | 擷取 | 碎片數 | 封閉區域 | 過小區域 | 黑填充 |
|---|---|---|---|---|---|
| 0.45 / 0.5 | luma | 101 ± 32 | 115 ± 35 | 49 ± 23 | 0.014 |
| 0.45 / 0.5 | xdog | 137 ± 55 | 181 ± 54 | 110 ± 41 | 0 |
| **0.55 / 0.6** | **luma** | **59 ± 17** | 94 ± 30 | **40 ± 22** | 0.011 |
| 0.55 / 0.6 | xdog | 139 ± 54 | 136 ± 50 | 82 ± 40 | 0 |
| 0.65 / 0.7 | luma | 80 ± 26 | 90 ± 16 | 46 ± 10 | 0.012 |
| 0.65 / 0.7 | xdog | 145 ± 58 | 124 ± 35 | 75 ± 25 | 0 |

- 生成耗時：每張約 6.7 s（第一張含模型載入 36.3 s）；擷取＋後處理約 0.5–1 s。
- `gray_pixel_ratio` 對擷取後的圖計算，一律為 0，不具比較意義。
- 和 C0 的 ip_weak 相比（同樣 0.55/0.6，但提示詞要求黑白線稿）：luma 碎片 90 → 59、過小區域 66 → 40，
  提示詞改成「彩色＋粗黑外框」讓外框更完整。

## 觀察（目視 park s303、kitchen s101、bedroom s101 / s202）

1. **IP-Adapter 越強，場景越少**：kitchen s101 在 0.45 有完整廚房（掛勺、餅乾、爐台），0.55 只剩爐台與鍋子，
   0.65 只剩角色。park 在三個檔位都有樹或鞦韆架，但 0.65 的構圖最像參考圖（正面站姿）。
2. **角色一致性三檔都不錯**（藍色鮑伯頭、兔耳、條紋上衣＋吊帶褲）；0.45 偶爾會掉條紋（bedroom s202 的吊帶褲變素色）、
   耳朵長度與顏色變化較大。0.55 看起來是場景與角色的平衡點，與使用者在 C0 的判斷一致。
3. **bedroom 夜景沒有生成成功**：27 張都沒有出現夜晚或深色背景（原圖平均亮度 180–209，與白天場景相同），
   只有部分出現床／窗戶，檯燈與書幾乎沒出現。推測是 `plain white background` 與 IP-Adapter 壓過了場景描述。
   **因此「深色背景下 luma 會不會把背景變成黑塊」這個問題本輪沒有驗證到**，黑填充低不能當作 luma 在夜景可用的證據。
4. luma 與 xdog 的差異與 C0 相同：luma 乾淨、色塊大；xdog 保留條紋與眼睛細節但多出不封閉的短線。
5. 角色常是正面站姿、置中，動作（盪鞦韆、烤餅乾、看書）多半沒有表現出來——只有 prompt 與 IP-Adapter，沒有姿勢控制。

## 結論

- Route C 管線可行，且穩定（54/54）。**IP-Adapter 0.55 / end_at 0.6 + luma** 指標最好，xdog 作為細節版。
- 一致性分數尚未評分：請開 `report.html` 評分、匯出 `scores.csv`，再執行 `run_matrix.py summarize`。

## 下一輪可驗證的假設

- 把 `plain white background` 拿掉、改成描述場景背景，場景（含夜景）能否出來？夜景時 luma 是否需要改用 xdog 或調門檻？
- 用 ControlNet（Route A 已接好，lineart / openpose）給姿勢或構圖，解決「永遠正面站姿」的問題。
