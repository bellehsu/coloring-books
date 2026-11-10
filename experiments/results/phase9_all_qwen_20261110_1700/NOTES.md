# Phase 9 Task 1: all-Qwen gate (E1 characters, E2 Chinese prompts), 2026-11-10

> The session that wrote the original plan stopped after preparing `run.py`, before running it. A takeover session ran the experiment (see the TAKEOVER section of the ledger).
> At the user's request, the experiment was **rerun in full** according to the plan. Changes from the original script:
> - Variation list VA was dropped, because its phrases "waving" and "arms open wide" had already been shown to cause side effects. It was replaced with VM, the three phrases that passed calibration in `phase9_qwen_calibration_20261110` on branch `phase9-qwen-only`.
> - The pose template is the final version T2 from `phase9_pose_location_20261110`: generic, with no hard-coded scarf, and "draw only the character, even if the action mentions a place".

## Setup
- ComfyUI 0.36.0 @ 8188, RTX 5080. The GPU was idle and `/queue` empty before starting; the models were unloaded with `POST /free` afterwards.
- `run.py`:
  - `char VM VG`: 6 Chinese descriptions × 4 seeds × 2 variation lists, 48 images.
  - `pose`: E1 = the chosen reference per description × waving/jumping. E2 = book 2's bunny (bunny51) and the girl × 6 actions × 3 template shapes (EN, ZM = Chinese inside the English template, ZH = fully Chinese template) × 2 seeds. Also the Phase 6 bunny (bunnyP6) × 6 actions in EN.
  - `bg`: 6 Chinese scenes, ZH shape.
  - The English and ZM backgrounds reuse the phase 8 VB and spike results.
- Contact sheets (downscaled) are in `sheets/`. Per-image data is in `results.csv`. Full-size images are in `processed/` (not version-controlled). The earlier spike is copied into `spike/`.
- Visual scoring was done by the takeover controller from the sheets (scale 1–5).

## E1 characters
Descriptions:
- bunny: 小白兔＋紅圍巾 (little white bunny with a red scarf)
- girl: 辮子女孩＋黃雨衣 (girl with plaits and a yellow raincoat)
- dino: 藍恐龍 (blue dinosaur)
- astro: 太空人 (astronaut)
- car: **紅色小汽車（有眼睛和笑臉）** (little red car with eyes and a smiling face)
- fox: 戴草帽背背包的狐狸 (fox in a straw hat with a backpack)

| Variation list | Usable (out of 4) | Visibly different | Notes |
|---|---|---|---|
| VM (with arm movements) | 4/4 for animals and children; car 2/4 | Yes | Phrases about arms give the **car humanoid arms and legs** (s1001 and s1004 become red little people) |
| **VG (generic: proportions, expression, slight turn)** | **4/4 for every description** (car s1002 stands on legs, but is still usable) | **Yes** | Vehicles and objects stay looking like themselves |

- White background 1.0, single figure, whole body visible, 16–24 s per image.

## E1 poses (EN, seed 101; `sheets/sheet_poses_E1.jpg`)

| Reference | Waving: consistency / action | Jumping: consistency / action |
|---|---|---|
| bunny51 (book 2's SDXL bunny with a red hood) | 4.5 / 5 | 4.0 / 5 |
| bunnyP6 (Phase 6 bunny) | 4.5 / 5 | 4.0 / 5 |
| bunny (VG) | 5 / 5 | 5 / 5 |
| girl | 4.5 / 5 | 5 / 5 |
| dino | 5 / 5 | 5 / 5 |
| astro | 5 / 5 | 4.5 / 5 |
| car | 4.5 / 5 | 4.5 / 5 |
| fox | 5 / 5 | 5 / 5 |

- Average consistency is about 4.7. No pose added accessories it shouldn't have, such as a scarf.
- Jumping images often have a small shadow ellipse or motion lines. These are separate pieces, and the production `largest_component` removes them.

## E2 Chinese
- **Poses (bunny51 and girl, 6 actions × 2 seeds):** the ZM shape (Chinese action inside the English template) draws the right content, the same as EN: holding, running, waving, jumping and reading are all correct.
  - **Sitting:** the girl sits in 6/6 images across all three shapes, with no ground drawn, and bunnyP6 sits in 2/2. **bunny51 never sits** (0/6 in EN, ZM and ZH alike), so its standing, hands-clasped reference resists the change. This is no worse than English.
  - ZH (fully Chinese template) has a similar line count, but the plan chooses one shape, so ZM is used.
- **Backgrounds** (`sheets/sheet_backgrounds*.jpg`):

| Shape | Gaps | Tiny regions | Content correct | People or animals |
|---|---|---|---|---|
| EN VB (phase 8) | 9.8 | 73.2 | 6/6 | 0 |
| **ZM (Chinese inside the English template)** | **8.6** | **74.5** | **6/6** | **0** |
| ZH (fully Chinese template) | 7.3 | 96.1 (+31%) | 6/6 | 0 |

## Gate

| Criterion | Result |
|---|---|
| At least 2 of 4 candidates usable for every description | **PASS** (VG: 4/4) |
| The 4 candidates are visibly different | **PASS** (VG) |
| Average pose consistency ≥ 3.5 | **PASS** (about 4.7) |
| Book 2's bunny ≥ 4.0 | **PASS** (4.25; it doesn't sit, but neither does EN) |
| Chinese content correct in ≥ 5 of 6 | **PASS** (6/6 backgrounds, pose actions as in EN) |
| Chinese gaps and tiny regions within 20% of EN | **PASS for ZM** (ZH tiny regions are +31%, so the ZH shape is not used) |
| No people or animals in backgrounds | **PASS** |

## Constants (used from Task 2 on)
- `QWEN_CHARACTER_TEMPLATE` = "Single full body front view of {description}, standing. {variation}Children's picture book illustration, flat colors, thick clean black outlines, simple shapes. Plain pure white background, no ground, no shadow, centered, whole body visible from head to feet. Only one character, no other characters, no character sheet, no multiple views, no text."
- `QWEN_CHARACTER_VARIATIONS` = VG: ("Big head and small body, gentle smile", "Slightly taller and slimmer proportions, big happy smile", "Chubby round shape, cheerful laughing expression", "Turned slightly to one side, playful happy expression")
- `QWEN_CHARACTER_SIZE` = (832, 1216)
- `QWEN_POSE_TEMPLATE` = T2 (see `takeover-ports.md`): generic, with "Draw only the character, even if the action mentions a place: no ground, no floor, no scenery, no background objects."
- Prompt shape: the user's Chinese or English text goes into the English template as written (ZM).
- Character creation (user's decision): 1 image per click, plus 「再產生一張」, which never reuses the previous variation.
