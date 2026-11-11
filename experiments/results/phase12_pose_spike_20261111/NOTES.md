# Phase 12 spike：動作變化、表情、面向、多手腳、樣板副作用（2026-11-11）

## 設定
- ComfyUI 0.36.0 @ 8188，RTX 5080；開始前 GPU 閒置，兩輪結束都 `POST /free`。
- 參考角色：Phase 9 VG 兔子／女孩。`qwen_edit_pose_cutout`，832×1216，Lightning 4 步（CFG 1.0：負面提示詞無效，只能用正面語句）。
- `run.py run|run2|report|report2`；縮圖表 `sheets/`，數據 `results.csv`，原圖 `processed/`（不進版控）。視覺評分由我看縮圖表。

## 第 1 輪（72 張）
| 組 | 張數 | 缺口 | 過小區域 | 秒 |
|---|---|---|---|---|
| 多手腳（兔子 16 動作、女孩 8 動作 × T3／T3A） | 48 | 2.1 | 27.7 | 23.8 |
| 表情 8 種 | 8 | 1.6 | 20.1 | 24.3 |
| 面向 3 × 2 動作 | 6 | 2.0 | 10.5 | 24.0 |
| 重新生成變化 | 10 | 1.0 | 12.0 | 24.4 |

- **多手腳：48 張都沒有看到多出的手腳**（兩個參考角色、16 個動作，含拍手、抱球、踢球、彎腰撿東西、伸懶腰、跳舞、吃蘋果、躺著睡）。
  加「Correct anatomy: exactly two arms…」（T3A）看不出差別；第 2 輪 T4A 有一張讓女孩的辮子不見（角色跑掉）→ **不加解剖語句**。
  使用者遇到的多手腳情況未重現，需要實例。
- **新動作 8 個都可用**：clapping hands、hugging a big ball、kicking a ball、bending down to pick something up、
  stretching both arms up high、dancing、eating an apple、lying down and sleeping（女孩踢球兩張都沒畫出球 △）。
- **表情 8 種都好**：開心、大笑、難過（藍色眼淚）、生氣、驚訝、害羞（腮紅斜線）、想睡、害怕；角色與紅圍巾都不變。
- **面向**：「facing toward the left/right side of the picture」讓臉與眼睛轉向，身體仍偏正面，效果中等；朝右的走路鼻子略突出。
- **重新生成**：只換 seed（101 vs 202）幾乎一模一樣（使用者回報的問題重現）。
  變化語 V1「three-quarter view turned slightly to the left」、V2「…to the right」差異最大；
  V3「bigger, more energetic motion」、V4「calmer, smaller motion」有可見差異但較小。

## 意外發現：T3 樣板（Phase 11）的副作用
- T3「plus the vehicle or object the action mentions」在動作沒提到物品時也會加：女孩「jumping happily」腳下多一台計程車；
  「holding something」變成拿玩具公車。

## 第 2 輪（24 張）：T4 只在動作寫到時才畫那一樣
- T4：「Draw only the character. If the action names a vehicle or an object, also draw that one thing, completely visible;
  if it names none, add nothing else; …」
- 女孩跳 3 個 seed：都沒有多出物品 ✓；「拿著東西」→ 一般的罐子／杯子（不再是交通工具）✓；
  消防車、公車（車外觀）、腳踏車、蘋果都照畫 ✓。T4A 一張女孩辮子消失 ✗。

## 結論（Phase 12 設計依據）
- 正式樣板改 T4（不加解剖語句）。
- 動作變化語：V1–V4，重新生成不重複上一張；有指定面向時只用 V3／V4。
- 表情快速鈕 8 個、動作快速鈕加 8 個（共 18），面向選擇用片語（效果中等）。
- 多手腳：本輪 0/72 + 0/24，等使用者提供實例再針對處理；擦除工具為暫時解法。
