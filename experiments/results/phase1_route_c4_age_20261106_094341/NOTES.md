# Phase 1 Route C 第四輪（C4）：依年齡層的提示詞（2026-11-06）

- 設定：`experiments/configs/phase1_route_c4_age.yaml`（run.json 有完整副本）
- 固定 Route C 預設：IP-Adapter 0.55 / 0.6、只畫道具的草圖（`pose/*_props.png`）ControlNet scribble 0.4 / 前 50%、擷取 xdog sigma 1.2。
  角色描述不變（含條紋吊帶褲）。
- 提示詞：
  - `base`：C3 props04 的提示詞（對照；參數與 C3 props04 相同，圖也相同）
  - `age2_6`：toddlers aged 2 to 6、big simple rounded shapes、large flat color areas、very thick bold outlines、minimal details、
    few large objects；負向加 intricate details / small objects / cluttered background / patterns / hair strands / sparkles 等
  - `age2_6_bold`：同一張 age2_6 彩圖，擷取改 xdog 1.5、線寬 16 px、碎片門檻 200（幼兒粗線版）
  - `age6_12`：kids aged 6 to 12、moderate amount of detail、full background scene with several objects；負向加 messy / hatching
- **36/36 格成功，無失敗**；實際生成 27 張，每張約 9.3 s（第一張含載入）。

## 指標（每列 9 張）

| 提示詞 | 碎片數 | 封閉區域 | 過小區域 | 黑填充 |
|---|---|---|---|---|
| base | 46 | 120 | 55 | 0.0008 |
| **age2_6** | **40** | **92**（−23%） | **41**（−25%） | 0.0007 |
| age2_6_bold | 40 | 108 | 60 | 0.0003 |
| age6_12 | 48 | 115 | 50 | 0.0008 |

age2_6_bold 的封閉區域反而變多：16 px 粗線把原本有缺口的地方（例如鞦韆繩與座板之間）封起來了，多出一些小區塊。

## 觀察（目視 park s101、kitchen s202、bedroom s303）

1. **age2_6 確實比較簡單**：背景物件變少（bedroom 的星星、窗戶、月亮都沒了），角色變大、佔畫面主體，畫面留白多。
   但**條紋仍在**（角色描述與參考圖都有條紋），是幼兒版剩下最多的細碎線來源。
2. age2_6 的代價：場景描述被弱化——bedroom 不再是夜晚；kitchen s202 有一隻兔耳脫離頭部、浮在旁邊（角色錯誤）。
3. **age2_6_bold 的粗線很適合幼兒**：線條明顯變粗、更像幼兒著色本，內容與 age2_6 相同。
4. **age6_12 與 base 差異不大**，背景物件略少（bedroom 的星星變少）；提示詞中的年齡字眼對 SDXL 影響有限，
   真正起作用的是 simple / minimal details / few objects 這類描述詞。
5. 一致性問題與提示詞無關：kitchen s202 四種提示詞的頭髮都變成橘藍混色，是該 seed＋場景的問題。

## 結論

- **2–6 歲：age2_6 提示詞＋粗線擷取（xdog 1.5、線寬 16 px）** 是可用的幼兒版設定；若要再更簡單，下一步是處理條紋（角色設計）。
- **6–12 歲：base 或 age6_12 都可以**，兩者差不多；建議用 age6_12（背景稍微收斂、語意清楚）。
- 年齡層可以做成一組「預設值」（提示詞＋負向提示詞＋擷取參數＋線寬），而不是單一參數。

## 決定與後續試做（2026-11-06）

使用者決定：**不用年齡提示詞**，生成維持 base；「衣服線條太複雜」改用擷取端的 xdog／線條參數處理。
以 base 的 park s101、kitchen s303 試做兩種擷取端方法（試做程式未納入專案）：

1. **剪短分岔線**（骨架化後移除一端懸空、長度 < 60／120／200 px 的分支，A4 300 DPI）：
   清掉零星短線（park 封閉區 133→126、kitchen 過小區 38→24），但**條紋不受影響**——條紋兩端都接在衣服外框上，不是懸空分支；
   ≥120 px 時睫毛與眼睛反光也被剪掉。
2. **抹平細的中間色調條帶**（灰階 closing k=5／9／13，深色 < 100 的外框保留）：
   park 上衣的淺色細條紋在 k9 被去掉、眼睛與外框保留；但 kitchen 吊帶褲的深藍粗條紋（灰階 < 100，被當成外框保留）完全沒變，
   而且烤箱窗、櫥櫃等淺色家具線被一起抹掉。

**結論：擷取端參數可以控制「整體」複雜度（xdog sigma、碎片門檻、剪分岔），但無法只去掉衣服條紋而不傷到其他淺色線。**
條紋是模型畫出來的內容，可靠的做法是在生成端改角色服裝（描述＋參考圖）。
