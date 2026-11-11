# Phase 12 實機驗收：重新生成變化、表情、面向、T4（2026-11-11）

## 設定
- ComfyUI 0.36.0 @ 8188，RTX 5080。開始前 GPU 閒置，結束後 `POST /free`。
- 分支 `phase12-pose`，用這個分支的正式函式：`qwen_pose_prompt`，以及排除上一張的 `pick_pose_variation(rng, exclude, facing)`，直接送 `qwen_edit_pose_cutout`。
- `run.py run|report`；縮圖表在 `sheets/sheet_acceptance.jpg`，原圖在 `processed/`（不進版控）。共 13 張，約 25 秒／張。

## 結果
| 項目 | 變化語 | 結果 |
|---|---|---|
| 揮手 × 3 | V4 → V1 → V4 | △ consecutive shots differ; shots 1 and 3 shared V4 and looked similar → fixed by excluding the last two variations (I3). 第 1 張用 V4「calmer」，嘴角變成微皺眉 |
| 揮手＋大笑 | V1 | ✓ 大笑＋側身揮手 |
| 跳舞＋害羞 | V4 | ✓ 腮紅、瞇眼、跳舞 |
| 走路＋朝左 × 3 | V4 → V3 → V4 | ✓ 三張都朝左；只換動作大小（facing 時只用 V3／V4），三張可分辨 |
| 女孩跳起來 × 2 | V1 → V4 | ✓ 沒有多出物品（T3 的計程車問題已修好） |
| 消防車／公車／腳踏車 | V1／V4／V3 | ✓ 都照畫，公車是完整外觀 |

- 門檻全部通過。缺口 0–16，過小區域 2–203（腳踏車最多，與 Phase 11 相同）。
- 觀察：V4「with a calmer, smaller motion」有 1/5 次讓表情變成微皺眉。若使用者在意，下一版可改寫成「with a smaller, gentle motion and the same happy face」，需重新校正。
