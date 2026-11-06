# Phase 1 Route C3（C 部分）：hybrid 擷取，對 C2 原圖重新擷取（2026-11-05）

- 設定：`experiments/configs/phase1_route_c3_reextract.yaml`；來源 `phase1_route_c2_20261105_170021` 的 54 張原圖（不重新生成）。
- 擷取：luma（門檻 90）、xdog（同前）、**hybrid**（新增，`app.services.lineart_extract`，預設參數）：
  1. 原圖以 sigma 12 模糊後亮度 < 120 的區域視為深色區，改用 xdog；
  2. 在 luma 的大片空白區域（白色連通區塊 ≥ 全圖 5%）補回 xdog 中外接框長邊 ≥ 120 px、附近 7 px 內沒有 luma 線的線段。
- 162/162 格成功。`run_matrix.py reextract` 修正為每張原圖只處理一次（C2 的 luma／xdog 共用原圖）。
- 參數是用 kitchen white_cn05 s101、bedroom scene_cn05 s202、park scene_none s202 三張調的；
  另外目視 bedroom white_cn05 s101、kitchen scene_none s303、park scene_cn07 s101 三張未參與調參的圖。

## 指標（54 張平均）

| 擷取 | 碎片數 | 封閉區域 | 過小區域 | 黑填充 | bedroom 黑填充最大值 | 擷取耗時 |
|---|---|---|---|---|---|---|
| luma | 72 | 78 | 37 | 0.0108 | 0.060 | 0.001 s |
| xdog | 107 | 113 | 64 | ≈0 | 0.0001 | 0.01 s |
| **hybrid** | **72** | 88 | 47 | 0.0012 | 0.010 | 0.03 s |

## 觀察

1. hybrid 的碎片數與 luma 相同，黑填充降約 90%；夜空窗（深藍底白星）由雜訊色塊變成乾淨的月亮與星星。
2. luma 漏掉的淺藍家具線（櫥櫃門、窗框）被補回；衣服條紋在被外框封閉的區域內，不會被補進來。
3. **失敗案例**：park scene_cn07 s101 整張圖幾乎沒有黑色外框（ControlNet 0.7 把外框壓成淺藍細線），
   hybrid 的兔耳與繩子變成虛線，這張 xdog 最好。試過兩種自動判斷（luma/xdog 墨水比例、xdog 線被 luma 覆蓋的比例）
   都無法把它和 hybrid 處理得好的圖分開（0.55 vs 0.56），所以**沒有加入自動退回 xdog 的規則**。
   根本原因在生成端：ControlNet 應 ≤ 0.5–0.6。

## 線條複雜度的旋鈕（同一張 kitchen scene_none s303 比較，A4 150 DPI 縮圖）

| 旋鈕 | 效果 |
|---|---|
| 擷取方式 luma → hybrid → xdog | 簡單 → 細（封閉區域 117 → 159 → 195） |
| xdog `sigma` 0.8 → 1.5 → 2.5 | 最平滑的細節旋鈕：過小區域 118 → 38 → 21，輪廓保留；條紋仍在 |
| luma `threshold` 60 → 90 → 130 | 60–90 可用；130 會把藍色頭髮填色當成線，畫面變亂 |
| 後處理 `min_fragment_area` 16 → 400 → 1500 | 刪掉短線；1500 時眼睛細節與鈕扣也被刪掉 |
| （試做）把小於 N px 的封閉區塊填黑再骨架化 | **不可用**：衣服與臉部結構被破壞，未實作 |
