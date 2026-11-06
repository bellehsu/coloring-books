# Phase 3：角色提示詞調整（2026-11-06）

## 問題
Phase 2 實測時，角色工作室產生的角色圖只有半身，背景是米色。目標是全身、白底，方便當 IP-Adapter 參考圖與線稿預覽。

## 設定
- workflow：`sdxl_text2img`（SDXL Base 1.0，30 steps、cfg 6、dpmpp_2m karras、832×1216）
- 角色：momo（藍色兔耳女孩，條紋吊帶褲）、bear（戴紅帽、穿黃雨衣的小棕熊）
- seed：11、22、33、44
- 共 5 組提示詞 × 2 角色 × 4 seed = 40 張；每張約 5.5–6 秒（RTX 5080）。圖在 `raw/`（不進版控），提示詞全文在 `run.json`

| 組別 | 正向重點 | 負向加入 |
|---|---|---|
| v0_current | 原本：`character reference, single full body front view…, plain white background` | 無 |
| v1_headtoe | `full body character reference…, head to toe, feet visible` | close-up、portrait、upper body、cropped、colored/beige background、scenery… |
| v2_weighted | `(full body:1.3), (white background:1.2)…` | 同 v1 |
| v3_isolated | 拿掉 `character reference`；`isolated on pure white background` | colored/blue/gray background、scenery、ground、grass、multiple views、character sheet… |
| v4_isolated_w | 同 v3，白底用權重 `(isolated on pure white background:1.3)` | 同 v3 |

## 結果（`results.csv`：圖片四邊 24 px 的平均亮度、亮度 > 235 的比例）

| 組別 | 邊緣平均亮度 | 白色比例 | 全身 | 觀察 |
|---|---|---|---|---|
| v0_current | 215.4 | 0.34 | 7/8 | 背景常是藍、灰色；熊有草地場景 |
| v1_headtoe | 198.5 | 0.42 | 6/8 | 2 張變成多視角設定圖（`character reference` + `head to toe` 觸發） |
| v2_weighted | 202.8 | 0.19 | 8/8 | 背景出現場景（花草、門口、室內） |
| v3_isolated | 216.1 | 0.35 | 8/8 | 沒有場景，但底色仍偏灰、米色 |
| **v4_isolated_w** | **230.1** | **0.50** | **8/8** | 最接近白底；momo 有 2 張仍是淺藍／淺灰底 |

這次 v0 也多半是全身，Phase 2 看到的半身應該跟 seed 有關；主要問題是背景顏色與場景。

## 決定
採用 v4，已寫入 `backend/app/services/prompts.py`（`character_prompt`、`CHARACTER_NEGATIVE`）。
淺色底仍會出現；角色圖只當 IP-Adapter 參考與線稿預覽用，淺色底可以接受。若要完全白底，之後可在後處理去背（不在本階段範圍）。
