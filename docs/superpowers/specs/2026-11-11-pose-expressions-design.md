# Phase 12：角色動作——重新生成有變化、表情、面向、更多動作（2026-11-11）

## 1. Why
The user reported three problems:
- Regenerating the same action gives an almost identical image.
- They want more expressions and more action buttons.
- Characters sometimes get extra limbs.

The spike `experiments/results/phase12_pose_spike_20261111/` found the following:
- The pose variation list is empty, so only the seed changes, and with Qwen Lightning a new seed changes almost nothing.
- Expressions and new actions work well.
- Facing works moderately.
- Extra limbs did not reproduce: 0 of 96 images.
- Phase 11's T3 template draws objects the action never mentioned, such as a taxi under a jumping girl, or a toy bus for "holding something".

## 2. User decisions (2026-11-11)
- Regenerating must give a visibly different pose.
- Add 表情 quick buttons and more 動作 quick buttons.
- Add a facing choice (朝左／正面／朝右). It is still worth having even though the effect is moderate; together with the compose flip, it is enough.
- Extra limbs: no new model and no anatomy sentence (the sentence changed the girl's hair). Use the eraser for now and wait for a real example.
- Writing a plan and building it directly is approved.

## 3. Design
### 3.1 Pose template T4 (replaces T3)
- Replace `QWEN_POSE_TEMPLATE` with the following, exactly:
  "Make this exact character {action}. Draw only the character. If the action names a vehicle or an object, also draw that one thing, completely visible; if it names none, add nothing else; no ground, no floor, no road, no scenery, no room, no other background objects, no text, no letters. Keep the same character design, proportions, colors, clothing and accessories unchanged. Full body, centered, plain pure white background, no shadow, no cast shadow, children's picture book illustration, flat colors, thick clean black outlines."
- Evidence from round 2:
  - jumping added no object in 3 of 3 runs;
  - "holding something" gives a plain cup or jar;
  - the fire engine, bus, bicycle and apple are still drawn.

### 3.2 Regeneration variation
- `QWEN_POSE_VARIATIONS` = the following, in this order:
  1. "seen in a three-quarter view turned slightly to the left"
  2. "seen in a three-quarter view turned slightly to the right"
  3. "with a bigger, more energetic motion"
  4. "with a calmer, smaller motion"
- Every pose job picks a variation at random. It excludes the variation used by **this page's most recent pose** generation, found by id regardless of status, as `_last_simple_variation` already does for backgrounds. This applies to single requests and batch requests alike.
- When the action text contains a facing phrase from §3.3, only variations 3 and 4 may be used, so the turn variations can't fight the chosen facing.
- The chosen variation is already stored in `request_json.variation`, and the prompt is stored too.

### 3.3 Quick buttons (frontend, appended to the action text)
- **動作** (`ACTIONS`): keep 站著, 坐著, 走路, 跑步, 揮手, 指向前方, 拿著東西, 跳起來 and 看書. **Remove 開心笑**, because it is now an expression. Add these:

  | Label | Phrase |
  |---|---|
  | 拍手 | clapping hands |
  | 抱大球 | hugging a big ball |
  | 踢球 | kicking a ball |
  | 彎腰撿東西 | bending down to pick something up |
  | 伸懶腰 | stretching both arms up high |
  | 跳舞 | dancing |
  | 吃蘋果 | eating an apple |
  | 躺著睡覺 | lying down and sleeping |

  That makes 17 action buttons. They add phrases the same way they do today.
- **表情** (new group, aria-label 「表情」). At most one is active. Choosing one replaces any other expression phrase already in the text, and choosing the active one removes it.

  | Label | Phrase |
  |---|---|
  | 開心 | with a happy smiling expression |
  | 大笑 | with a big laughing expression |
  | 難過 | with a sad expression |
  | 生氣 | with an angry expression |
  | 驚訝 | with a surprised expression |
  | 害羞 | with a shy blushing expression |
  | 想睡 | with a sleepy expression |
  | 害怕 | with a scared expression |

- **面向** (new group, aria-label 「面向」). The same single-choice rule applies.

  | Label | Phrase |
  |---|---|
  | 朝左 | facing toward the left side of the picture |
  | 正面 | facing the viewer |
  | 朝右 | facing toward the right side of the picture |

  A hint below the group: 「臉會轉向，身體可能仍偏正面；不夠時可在構圖用左右翻轉」.
- All three groups respect MAX_ACTION (300). A button that would push the text over the limit is disabled, as action buttons are today.
- The groups appear wherever the action presets appear today: the editor's pose card. The 故事 rows have no preset buttons, so nothing is added there.
- The backend detects facing phrases with the exact English phrases above, matched case-insensitively. Typed Chinese like 「朝左」 is not detected, which only means all four variations stay allowed.

## 4. Success criteria
- Unit tests:
  - the T4 constant;
  - the variation list;
  - exclusion of the previous variation, for both single and batch requests;
  - facing restricts the choice to variations 3 and 4;
  - the frontend groups, with single-choice replacement, toggling off and the length limit;
  - the actions list.
- e2e: in the editor, add an expression and a facing, then switch to another expression; the text has exactly one expression phrase. The pose card at 390 px has 0 px horizontal overflow.
- Real-GPU acceptance (ask first, check the GPU is idle, `POST /free` afterwards):
  - the bunny regenerating 揮手 three times gives three visibly different poses;
  - 揮手＋大笑 and 跳舞＋害羞 combinations work;
  - 朝左 with two regenerations varies only in motion size;
  - girl 跳起來 has no extra object;
  - three vehicle-pack actions are still drawn: 消防車, 公車 and 腳踏車.
  - Write the results to `experiments/results/phase12_pose_acceptance_<ts>/NOTES.md`.

## 5. Calls made by the author
- 開心笑 is removed from the actions.
- Expression and facing are phrases in the action text rather than new database fields, so the batch flow and the existing storage work unchanged.
- The four variation phrases are used verbatim from the spike.
