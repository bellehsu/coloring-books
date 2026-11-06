# Phase 1 Route C 第二輪（C2）：場景背景 × 姿勢控制（2026-11-05）

- 設定：`experiments/configs/phase1_route_c2.yaml`（run.json 有完整副本）
- 生成：`route_a_sdxl_ipadapter_cn`，IP-Adapter 固定 0.55 / end_at 0.6（C1 的最佳值），steps 30、cfg 6、832×1216。
- 因素：
  - 背景：`white`（提示詞含 plain white background，同 C1）vs `scene`（改成 full detailed background scene，負向加 plain/empty/white background）
  - 姿勢：`none`（ControlNet 0）vs `cn05`（Union scribble 模式，0.5，前 50%）vs `cn07`（0.7，前 70%）
  - 姿勢草圖：`experiments/samples/pose/{park_swing,kitchen_baking,bedroom_reading}.png`，由 `experiments/pose_sketches.py` 以程式繪製
    （大頭身比例的火柴人＋道具；Route A workflow 會反相，所以畫白底黑線）。
  - 擷取：luma（門檻 90）、xdog（同 C1）
- 場景提示詞改成和草圖一致的動作（坐鞦韆抓繩、端一盤餅乾站在烤箱旁、夜裡坐在床上看書），所以 `white_none` 不等於 C1，只當組內對照。
- **108/108 格成功，無失敗**；實際生成 54 張（luma／xdog 共用）。生成每張約 6.6 s（無 ControlNet）／7.6–8.3 s（有 ControlNet）。
- ComfyUI 0.36.0 臨時開在 127.0.0.1:8190，`APP_COMFY__URL` 覆寫。

## 指標（每列 9 張：3 場景 × 3 seeds；luma）

| 變體 | 碎片數 | 封閉區域 | 過小區域 | 黑填充 |
|---|---|---|---|---|
| white_none | 91 ± 101 | 105 ± 43 | 51 ± 32 | 0.013 |
| white_cn05 | 44 ± 29 | 55 ± 12 | 21 ± 6 | 0.009 |
| white_cn07 | 40 ± 21 | 36 ± 12 | 13 ± 5 | 0.004 |
| scene_none | 98 ± 33 | 153 ± 81 | 84 ± 76 | 0.013 |
| scene_cn05 | 110 ± 133 | 78 ± 35 | 37 ± 25 | 0.017 |
| scene_cn07 | 50 ± 48 | 43 ± 11 | 16 ± 9 | 0.010 |

xdog 數值見 `summary.md`。**注意：有 ControlNet 時碎片與過小區域變少，主要是因為圖變簡單、而且 luma 漏掉了淺色的線（見觀察 3），不是品質變好。**
bedroom 的 luma 黑填充：scene_cn05 平均 0.040（最高 0.060）、scene_cn07 0.023（最高 0.050），其他變體 ≤ 0.021。

## 觀察（目視 park s202、kitchen s101、bedroom s202 的六個變體，luma 與 xdog）

1. **ControlNet scribble 確實控制住構圖與動作**：坐在鞦韆座板上、手端烤盤、坐在床上，床頭板／窗戶／檯燈的位置都照草圖出現。
   無 ControlNet 時仍是正面站姿或坐地上（C1 的問題）。
2. **但角色被草圖拉壞**：程式畫的火柴人太簡陋，模型把它照抄——身體變成蛋形、腿變成細棍、角色變小；cn07 比 cn05 嚴重。
   kitchen s101 cn07 甚至把攪拌碗畫成第二顆兔子頭。cn05 是可接受的上限，但角色品質仍明顯低於無 ControlNet。
3. **luma 遇到細線或淺色線會漏線**：ControlNet 版本的外框較細、家具常是淺藍色線，luma（只取深色）把櫥櫃、床邊線條弄斷
   （kitchen s101 white_cn05：櫥櫃只剩把手）。**xdog 在這些圖上明顯較好**，能保留家具線條。
4. **夜景**：只有加了 ControlNet（草圖有窗戶、月亮、星星）時才出現夜空窗戶；整個房間變暗的夜景仍沒有出現。
   夜空窗戶是深藍色＋白色星星：**luma 把它變成一團雜訊色塊**（scene_cn05 黑填充最高 0.06），
   **xdog 則乾淨地畫出月亮與星星**。C1 預測的「深色區域會讓 luma 失效」得到驗證。
5. **scene 提示詞（無 ControlNet）能帶出背景**：park 有大樹與輪胎鞦韆、kitchen 有掛勺架與爐台，角色品質不受影響。
   但 bedroom 只出現一堆被子，沒有床和窗。
6. xdog 的老問題仍在：條紋（衣服、床單）變成一排排不封閉的短線。

## 結論

- **姿勢問題：ControlNet 方向正確，但草圖品質是瓶頸。** 用程式畫的火柴人會把角色比例拉壞。
- **背景問題：拿掉 plain white background 有效**（scene_none），夜景要靠構圖控制才出得來。
- **擷取方式要看圖：** 有粗黑外框、淺色背景 → luma；有細線、淺色線或深色區域（夜景）→ xdog 較穩。單一固定門檻的 luma 不夠通用。
- 一致性分數尚未評分：請開 `report.html` 評分，匯出 `scores.csv`，再執行 `run_matrix.py summarize`。

## 下一輪可驗證的假設

- **草圖只畫道具與背景、不畫角色**（角色區域留白），讓 ControlNet 只控制場景，角色交給 IP-Adapter 與提示詞——角色比例能否恢復？
- 換成較像樣的草圖：由使用者手繪，或從一張滿意的生成圖擷取 xdog 線稿當草圖。
- ControlNet 0.3–0.4、只作用前 30–40%，找角色不被拉壞的強度。
- 擷取改成 luma 與 xdog 的混合（例如 luma 為主、xdog 補上 luma 漏掉的長線），或依原圖暗部比例自動選擇。
