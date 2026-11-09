# 背景提示詞校正（Task 6，2026-11-09）

只寫文字（無構圖草圖）的背景，用新 `sdxl_background` workflow；三個場景 × 兩個 seed × 三種寫法。
每張背景轉成 A4 家用線稿（standard、11 px）後算 QA。原圖與總覽在 `processed/`（不進版控），逐張數據在 `results.csv`。

| 寫法 | 說明 | 平均缺口 | 平均過小區域 | 黑色比例 | 每張秒數 |
|---|---|---|---|---|---|
| A_current | Task 2 初版：「simple uncluttered composition with a few large objects」 | 67.3 | 448 | 0.238 | 14.2（含首次載入模型） |
| B_simple | 「simple coloring book background, a few large simple objects, lots of empty space」＋負向壓制繁複細節 | 52.2 | 182 | 0.174 | 9.0 |
| **C_weighted** | B 再加權 `(simple coloring book background:1.2)`、`(a few large simple objects:1.3)` | **42.0** | 188 | 0.170 | 10.2 |

目視：A 樹叢、花叢、廚房器具很密；B、C 留白明顯變多，C 的公園與廚房最乾淨。

決定：採用 C，寫入 `backend/app/services/prompts.py`（`background_prompt`、`BACKGROUND_NEGATIVE`）。
臥室場景仍偏繁複（書櫃、窗外風景），需要時可在畫板畫構圖或改寫場景描述；缺口仍交給自動補線。
