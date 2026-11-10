# 只用 Qwen-Image-Edit-2511（拿掉 SDXL）＋中文提示詞 spike（Phase 9，2026-11-10）

問題：想把 app 簡化成只用 Qwen-Image-Edit-2511、移除所有 SDXL 路線，並讓所有提示詞欄位接受中文。在決定之前要先確認：
(A) Qwen 能不能從文字描述做出好的**角色參考圖**（英文、中文）；(B) 這些 Qwen 角色當精準動作的參考圖好不好用；
(C) 背景與動作用中文寫行不行。

結論先講：**可以拿掉 SDXL。** Qwen 角色圖品質全面勝過 SDXL（目視 4.75–5.0 vs 2.9、白底 100% vs 38%），中文描述、中文場景、中文動作都看得懂。
但要先處理三件事（都不是 Qwen 本身的限制，而是提示詞與後處理）：
1. **候選圖幾乎一樣**：4 個 seed 只差在小細節 → 必須加「候選變化語」（本次 VENV/VZHV 清單可用，差異度翻倍、品質大致維持）。
2. **BiRefNet 遮罩破洞**：Qwen 風格的白色身體／頭盔內的臉被當成背景挖掉（Qwen 參考圖的動作 8/16 張破洞，SDXL 參考圖 0/8）→ `largest_component` 要加「補洞」，補洞後完全救回。
3. **`QWEN_POSE_TEMPLATE` 寫死了「scarf and ears」**：沒有圍巾的角色會被加上紅圍巾（恐龍 6/6、女孩中文版 5/6）→ 樣板要改成通用措辭。

## 設定
- ComfyUI 0.36.0 @ 8188、RTX 5080 16 GB。每批開始前等 GPU util ≤10% 且 `/queue` 空（`gpu_idle_wait`，同 phase6）；結束後 `POST /free` 卸載模型。
- 全部走正式 workflow（`WorkflowRegistry(...).get(name).build_prompt(values)`），沒有新增 workflow 或節點：
  - SDXL 角色基準：`sdxl_text2img`＋`prompts.character_prompt`＋`CHARACTER_NEGATIVE`，manifest 預設（30 steps、cfg 6、dpmpp_2m karras），832×1216（`CHARACTER_SIZE`）。
  - Qwen 角色：`qwen_background`（Qwen 純文字生圖，同一張圖，只換提示詞），832×1216。
  - 動作：`qwen_edit_pose_cutout`＋`prompts.qwen_pose_prompt`，832×1216，seed 101；遮罩先做最大連通塊（同 `compose.largest_component`）。
  - 背景：`qwen_background`＋`prompts.qwen_background_prompt`，864×1216，線稿指標 `render_base(img, (2244, 3272), STANDARD, 11)` → `qa.evaluate`（同 phase7/8）。
- 角色 seed 1001–1004（app 是 base＋i 連號）。4 個角色描述（英文／中文）：
  bunny「一隻圓圓的小白兔，長耳朵，圍著紅色圍巾」、girl「綁兩條辮子的小女孩，穿黃色雨衣和雨鞋」、
  dino「一隻友善的小藍恐龍，身上有圓點」、astro「穿白色太空衣、戴圓頭盔的小男孩太空人」。
- 腳本 `run.py`；逐張數據與目視評分 `results.csv`；圖、遮罩、總覽、`metrics.json`、`scores.json` 在 `processed/`（不進版控）。
- GPU 實際時間合計約 44.6 分（角色 23.7、動作 13.4、背景 6.6、暖機 0.8）。比預定 30–40 分多約 5 分，因為候選變化語多跑了 32 張；沒有刪減 seed。

### Qwen 角色樣板（cfg 1 → 排除條件寫在正向）
- **QEN／QZM（英文樣板）**：`Single full body front view of {desc}, standing. {variation}Children's picture book illustration, flat colors, thick clean black outlines, simple shapes. Plain pure white background, centered, whole body visible from head to feet. Only one character, no other characters, no character sheet, no multiple views, no text.`
  QEN＝英文描述；QZM＝中文描述原樣塞進英文樣板。
- **QZH（全中文樣板）**：`{desc}。單一角色的全身正面圖，站立。{variation}兒童繪本插畫，平塗色彩，粗而乾淨的黑色輪廓線，簡單的造型。純白色背景，角色置中，從頭到腳整個身體都在畫面內。只有這一個角色，沒有其他角色，不是角色設定圖，沒有多個視角，沒有文字。`
- **候選變化語**（QENV／QZHV；第 i 張候選用第 i 個，4 張各不同）：
  - EN：`Big head and small body, arms relaxed at the sides, gentle smile`／`Slightly taller proportions, waving one hand, big smile`／`Chubby round shape, hands together in front, shy smile`／`Arms open wide, cheerful laughing face`
  - ZH：`大頭小身體，雙手自然垂在身體兩側，溫柔的微笑`／`身形稍微高一點，揮著一隻手，大大的笑容`／`圓滾滾的體型，雙手在身前合起來，害羞的微笑`／`張開雙臂，開心大笑的表情`

## A. 角色參考圖（每版 4 描述 × 4 seed＝16 張；SDXL_ZH 只跑 seed 1001）

總覽：`processed/sheet_characters.jpg`（列＝描述；欄＝SDXL EN ×4、Qwen EN ×4、Qwen ZH ×4）、
`processed/sheet_characters_zh_variants.jpg`（QZM ×4、QZH ×4、SDXL ZH）、`processed/sheet_characters_variation.jpg`（QEN、QENV、QZHV）。

| 版本 | 白底（四邊 24 px 白色比例） | 單一角色（目視） | 全身（目視） | 多視角／設定圖 | 目視 1–5 | 時間／張 | 4 張候選差異度* | 目視：候選差異 |
|---|---|---|---|---|---|---|---|---|
| SDXL EN（現行） | 0.38（>0.95 只有 3/16） | 16/16 | 15/16（girl s1004 半身） | 0 | 2.94 | 6.1 s | 0.250 | 明顯不同（但多半是背景不同） |
| SDXL ZH | 0.00 | 4/4 | 4/4 | 0 | **1.0** | 5.5 s | – | 看不懂中文：4 張都畫成古裝女孩／武士 |
| **Qwen EN**（QEN） | **1.00** | 16/16 | 16/16 | 0 | 4.75 | 13.3 s（中位 10.3） | 0.089 | **幾乎一樣**（只差腮紅、髮色深淺） |
| Qwen ZH 描述＋英文樣板（QZM） | 1.00 | 16/16 | 16/16 | 0 | 4.75 | 15.7 s（中位 14.1） | 0.083 | 幾乎一樣 |
| Qwen 全中文（QZH） | 1.00 | 16/16 | 16/16 | 0 | **5.0** | 12.3 s（中位 9.8） | 0.090 | 幾乎一樣 |
| **Qwen EN＋變化語**（QENV） | 1.00 | 16/16 | 16/16 | 0 | 4.56 | 22.6 s | **0.171** | **明顯不同**（姿勢、表情、體型） |
| Qwen ZH＋變化語（QZHV） | 1.00 | 16/16 | 16/16 | 0 | 4.56 | 17.6 s | 0.162 | 明顯不同 |

\* 4 張縮成 64×94 灰階後兩兩平均絕對差（0–1）。SDXL 的數字大多來自背景顏色與場景不同，不代表角色設計不同。

- 「單一角色」以目視為準。自動指標（非白色區域的連通塊數，≥1% 面積）在 Qwen 有 6 張算出 2–4 個：白色身體貼白底，粉紅耳朵內側等色塊被切成獨立塊（假警報），目視全部是單一角色。SDXL dino s1004 算出 2 個是背景雜物。
- 「碰到邊」（角色框貼到圖邊）：SDXL 11/16（因為有色背景整片被算進去），Qwen 0/80。
- SDXL 問題同 phase3：灰、米、藍綠、黑色背景、雨滴、彩色圓點、地面；一張黑白素描加邊框；一張半身。
- **中文理解**：Qwen 中文描述完全正確，而且更貼字面——「辮子」畫成麻花辮（英文 pigtails 畫成雙馬尾），QZH 的兔子比英文版更「圓圓的」。QZM（中文描述放英文樣板）與 QZH（全中文樣板）品質相當，只有兔子的圓度差一點。
- 小瑕疵：QZM astro s1002 腳下有淡地面陰影。
- **變化語的副作用**（目視扣分處）：
  - 「hands together / 雙手合起來…害羞」→ QZHV 恐龍畫成掉眼淚；
  - 「arms open wide / 張開雙臂」→ 女孩腳下多水窪線（兩版都有）；
  - 「waving one hand / 揮著一隻手」→ 女孩頭旁多動態線或一團小雜物（兩版都有）；QZHV astro 腳下黑色地面陰影；QENV 恐龍手邊多一個小雜物。
  - 其餘 26/32 張與無變化語版本一樣乾淨。
- **時間**：Qwen 同一提示詞第 2 張起約 10 s；每換一次提示詞（第 1 張）約 20 s（文字編碼器重算＋顯存搬移）。變化語讓 4 張的提示詞都不同 → 每張約 18–23 s，4 張候選約 75–90 s（SDXL 4 張約 24 s，但 Qwen-only 省掉 SDXL↔Qwen 換模型）。

## B. 下游：Qwen 角色當精準動作參考圖

每個描述挑 QEN、QZH、SDXL EN 各一張參考（`run.py` 的 `PICKS`），做 2 個動作（waving one hand／jumping happily，即 app 的「揮手」「跳起來」），seed 101，`qwen_pose_prompt`。
總覽：`processed/sheet_poses.jpg`（原始 OUTPUT）、`processed/sheet_poses_mask_holes.jpg`（遮罩去背 vs 補洞去背）。

| 參考圖來源 | 一致性 1–5 | 動作 1–5 | 時間／張 | 遮罩破洞 >5% | 主要問題 |
|---|---|---|---|---|---|
| SDXL EN | 3.75 | 5.0 | 25 s | 0/8 | 恐龍被加圍巾、頭型變成長耳朵（2 分）；其他 4–5 分 |
| **Qwen EN** | **4.5** | 5.0 | 25 s | **4/8**（bunny、astro） | 恐龍被加圍巾（3 分）；其他全 5 分 |
| Qwen ZH | 4.0 | 5.0 | 26 s | 4/8（bunny、astro） | 女孩、恐龍被加圍巾（3 分）；其他全 5 分 |

- **Qwen 角色當參考圖沒問題**：臉、顏色、服裝、比例保留得比 SDXL 參考更好（Qwen 參考本身就是乾淨的平塗白底，動作圖直接沿用同一畫風）。動作 24/24 都正確。
- **圍巾問題（現行樣板的 bug，與 SDXL/Qwen 無關）**：`QWEN_POSE_TEMPLATE` 的「Keep the same … colors, scarf and ears unchanged」是照 phase6 兔子校正的，對沒有圍巾的角色會**加上紅圍巾**（三種參考圖的恐龍 6/6；女孩 QZH 參考 2/2、中文動作 3/4）。建議改成通用措辭，例如
  `Keep the same character design, face, proportions, colors, clothing and accessories unchanged; do not add new clothing or accessories.`（**未測**，下一步要用兔子＋恐龍＋女孩重新校正）。
- **BiRefNet 遮罩破洞（會直接影響合成頁）**：Qwen 平塗風格的白色身體（兔子）與頭盔內的臉（太空人），BiRefNet 只抓到外框與有色部分，內部被當成背景
  （`hole_ratio` 兔子 0.56–0.66、太空人 0.39–0.71；太空人的臉、兔子的臉被挖掉）。SDXL 參考圖的動作因為有灰階陰影，0/8 破洞。
  在合成頁上這些洞會透出背景。**補洞**（最大連通塊 floodFill 補內部洞）後完全救回（`_cutfill`）。
  建議 `compose.largest_component` 加補洞（未改正式程式碼）。代價：手叉腰之類真正的空隙也會被填成白色，對著色本影響小。
- 線稿指標（gaps／small_regions，見 `results.csv`）是用**破洞的去背圖**算的，兔子、太空人那幾張不具比較意義，所以這次不拿來比較。

## C. 中文

### 背景（`qwen_background_prompt(中文場景)`，與 phase8 英文同 seed、同變化語分配）
總覽：`processed/sheet_backgrounds_zh.jpg`、`processed/sheet_backgrounds_zh_lineart.jpg`（欄＝EN V0、ZH Z0、EN VB、ZH ZB 各 2 seed）。

| 版本 | 平均缺口 | 平均過小區域 | 時間／張 | 人／動物 | 內容符合 |
|---|---|---|---|---|---|
| EN V0（phase8） | 9.5 | 82.3 | 15.3 s | 0/12 | – |
| **ZH Z0**（無變化語） | **15.4** | 78.4 | 14.9 s | 0/12 | 12/12 |
| EN VB（phase8，正式） | 9.8 | 73.2 | 19.8 s | 0/12 | – |
| **ZH ZB**（VB 變化語，正式路徑） | **8.6** | 74.5 | 18.3 s | 0/12 | 12/12 |

- 中文場景完全看得懂：鞦韆／大樹／花、櫥櫃／烤箱／碗和餅乾、床／書架／窗、沙堡／水桶／海灘傘、課桌／黑板／窗、樹／蘑菇都有，構圖與英文版幾乎同一套；VB 變化語（英文）接在中文場景後一樣能左右換位置。
- ZH Z0 平均缺口 15.4 超過 spec §3.5 的 11.5，但來自 4 張離群（kitchen s101 37、beach s101 33、park s101 29、classroom s202 28），英文版也有 16–27 的單張；正式路徑（一定帶變化語）ZH ZB 8.6 反而比 EN 好。合計 24 張：中文 12.0／76.5 vs 英文 9.7／77.8，差異在單張變異範圍內。
- 中文「陽光公園」「陽光海灘」比英文 sunny 更常畫出**太陽和光芒線**（公園：中文 4/4 有太陽、3/4 有光芒線；英文 2/4 有太陽），可能是公園缺口偏高的原因。可以接受，或在中文說明裡建議寫「晴天的公園」。

### 動作（bunny、girl 的 QEN 參考；揮手／跳起來；seed 101）
總覽：`processed/sheet_poses_zh.jpg`（欄＝參考、英文動作、中文動作塞英文樣板、全中文樣板）。

| 版本 | 一致性 | 動作 | 備註 |
|---|---|---|---|
| EN（`qwen_pose_prompt("waving one hand")`） | 5.0 | 5.0 | – |
| ZM（`qwen_pose_prompt("揮手")`） | 4.5 | 5.0 | 女孩揮手被加條紋圍巾 |
| ZH（`讓這個角色{action}。保持完全相同的角色設計、比例、顏色、圍巾和耳朵不變。…`） | 4.0 | 4.75 | 女孩 2/2 被加圍巾；兔子跳得較不明顯 |

- 中文動作「揮手」「跳起來」放在英文樣板裡就能正確理解，**不需要全中文樣板**（全中文版沒有更好，圍巾問題反而更嚴重：中文「圍巾」字面更強）。
- 圍巾問題的根本原因同 B：樣板寫了 scarf。

## 建議

1. **可以拿掉 SDXL**：角色建立改用 Qwen（`qwen_background` 這張圖即可，或複製成 `qwen_character` 換 manifest 名稱），背景與動作本來就是 Qwen。沒有發現「中文被誤讀」的阻礙；SDXL 完全看不懂中文（畫成古裝人物），反而是改 Qwen 的理由。
2. **角色樣板**：採用英文樣板（QEN 那句），使用者的描述**不翻譯、原樣塞進去**（QZM 證明中文描述放英文樣板品質與全中文樣板相當，一套樣板不必偵測語言）。
   全中文樣板（QZH）兔子較圓，但差異只在 1 個描述，不值得維護兩套。
3. **候選變化語（必要）**：沒有變化語 4 張幾乎一樣，app 的「4 張候選」失去意義。採用 QENV 清單（英文，接在描述後），並先修掉副作用再上線：
   「shy smile」改成「happy smile」，「arms open wide」與「waving one hand」後面加「nothing on the ground」之類（**未測**，建議下一輪 4 描述 × 4 張再驗一次）。
   代價：每張多約 10 s（4 張候選約 75–90 s）。
4. **動作樣板改通用措辭**（拿掉 scarf and ears），用兔子、恐龍、女孩重新校正；中文動作直接塞進英文樣板即可。
5. **`compose.largest_component` 加補洞**：Qwen-only 之後角色都是平塗白底風格，白色身體、頭盔臉的破洞會常見，這是上線前必修。
6. **背景**：中文場景直接塞進現行 `qwen_background_prompt`，不需改樣板。
