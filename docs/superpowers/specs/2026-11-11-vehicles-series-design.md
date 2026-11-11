# Phase 11: vehicles series — bilingual captions, theme pack, character + vehicle (2026-11-11)

## 1. Why
The user wants a learning series about vehicles (交通工具) that works for parents at home and for kindergarten teachers. Each page shows the book's fixed character with a different vehicle, with a Chinese line and an English line under the picture. The spike `experiments/results/phase11_vehicles_spike_20261111/` showed the current pose step (Qwen edit) already draws "character + whole vehicle" for 7 of 8 vehicles, so the phase is mostly text plus a ready-made page list, not a new generation route.

## 2. User decisions (2026-11-11)
- **Theme:** the vehicles series comes first. It is for **both** home use (parent and child) and classroom use (teacher prints many copies).
- **Picture:** the fixed book character plus a different vehicle on each page, e.g. the bunny driving a fire engine. There is no separate object subject.
- **Text:** two lines on each page, Chinese then English. They are written together with the story, either in the paste import or edited on the page. There is **no automatic translation and no language model**.
- **Text style:** **hollow letters** (outlined, white inside) so children can colour them.
- **Font:** **Noto Sans TC (思源黑體)**, OFL 1.1. It is copied from the user's Windows fonts folder (`/mnt/c/Windows/Fonts/NotoSansTC-VF.ttf`) into the repo with its licence.
- **Theme pack:** one built-in pack, 「交通工具」, can be applied in one click and edited afterwards. More packs, such as animals or fruit, may follow later.

## 3. Goals, non-goals, anti-goals
- **Goals:**
  - Captions are stored per page and printed in the PDF, `print.png` and the shared view.
  - The paste import reads captions.
  - The built-in vehicles pack fills the story in one step.
  - The pose template is widened so vehicles and held objects are drawn on purpose.
  - The vehicles pack passes a real-GPU acceptance.
- **Non-goals:**
  - Multiple characters per page.
  - Reusing a character across books (a "series" of books).
  - Object subjects without a character.
  - Translation.
  - More theme packs.
  - Caption fonts other than Noto Sans TC.
- **Anti-goals:**
  - Pages without captions must print exactly as today: same picture size, no empty band.
  - Captions must never cover the picture.
  - The pose template change must not make ordinary actions draw ground or scenery.

## 4. Design

### 4.1 Captions (data)
- Alembic 0010 adds `page.caption_zh: str | None` and `page.caption_en: str | None`.
  - Values are trimmed, and empty becomes null.
  - Limits are **12** characters for Chinese and **28** for English. Longer input → 422 with zh-Hant messages 「中文字最多 12 個字」 and 「英文字最多 28 個字元」.
- `PageUpdate` accepts both fields, and `PageOut` returns them.
- Changing a caption doesn't touch generations, line art or QA status. A passed page stays passed, because the caption is printed beside the art rather than drawn into it.
- The shared detail (`/api/shared/books/{id}`) doesn't need the fields, because the shared page image already includes them.

### 4.2 Captions (print)
- `composed_page` (backend `api/export.py`) is the single place where captions are added, so the PDF, `print.png`, the shared image and the shared PDF all get them.
- **Layout.** This applies only when the page has at least one caption:
  - A band at the bottom of the content area takes **15%** of the content height. In print-shop mode the band sits inside the safe box.
  - The line art is scaled down, keeping its aspect ratio, to fit the area above the band and is centred horizontally.
  - The Chinese line is centred at 30% of the band height with a glyph size of about 50% of the band. The English line is centred at 78% with a glyph size of about 30%.
  - If a line is wider than 90% of the content width, its font size shrinks until it fits.
  - A single line, with only Chinese or only English, is centred vertically in the band at the Chinese size.
- **Hollow letters.** The font is Noto Sans TC, variable instance **Black**. Letters are drawn with a white fill and a black stroke.
  - The stroke width is about the export line width (`LINE_W` ≈ 11 px at 300 DPI), so the outline matches the line art. The spike used 7 px; the builder picks a value between 7 and 11 that keeps Chinese counters open, and records it.
  - The result goes through the same 1-bit conversion as the art.
- **Font file.**
  - The font goes in `assets/fonts/NotoSansTC-VF.ttf`, with `assets/fonts/OFL.txt` holding the SIL OFL 1.1 text and the copyright line from the font: 「© 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.」.
  - The path comes from settings `paths.caption_font`, defaulting to that file, following the CLAUDE.md path rule.
  - Startup and preflight report a missing or unreadable font in the gap list (preflight must not fail as a whole).
  - Exporting a page that has captions while the font is missing → 409 「找不到字型檔，無法印出頁面文字」.
- **Editor preview.** The editor shows the two captions as text under the canvas in both steps. When a caption exists, it also shows a 「預覽列印頁」 link that opens `print.png`.

### 4.3 Captions (input)
- **Paste import** (`frontend/src/lib/storyImport.ts`):
  - New labels: `中文` means the Chinese caption, and `英文` or `English` (case-insensitive) mean the English one. Both accept full-width or half-width colons.
  - Spreadsheet columns use the same names.
  - The import follows the existing fill-empty-only rule per field, and the preview shows the caption columns.
  - Over-limit captions are reported as row problems using the §4.1 messages.
- **AI prompt** (`aiPrompt.ts`): two optional lines are added to the format: 「中文：（選填，印在圖下方的短字，12 字內）」 and 「英文：（選填，上一行的英文，28 個字元內）」.
- **故事 page rows and the editor's ① 構圖 panel:** two inputs, 「中文字」 and 「英文字」, showing the remaining-character count. Changes save the same way the scene and action fields save today.

### 4.4 Pose template: T3
- Replace `QWEN_POSE_TEMPLATE` with T3 from the spike, plus "no text, no letters" because one image contained the garbled text "PALICE":
  - Old: "Draw only the character, even if the action mentions a place: no ground, …"
  - New: "Make this exact character {action}. Draw only the character, plus the vehicle or object the action mentions, completely visible; no ground, no floor, no road, no scenery, no room, no other background objects, no text, no letters. Keep the same character design, proportions, colors, clothing and accessories unchanged. Full body, centered, plain pure white background, no shadow, no cast shadow, children's picture book illustration, flat colors, thick clean black outlines."
- The spike's evidence for T3:
  - Vehicles are as good as with T2 or better.
  - Tiny regions fell from 122 to 75.
  - Ordinary actions didn't regress: sitting draws no ground, and holding or reading keeps the object.
- The "no text" addition hasn't been verified on the GPU yet. Acceptance (§5) checks it.
- Update `tests/test_prompts.py` to the new constant.

### 4.5 Theme pack 「交通工具」
- The pack is a data file in the frontend, `frontend/src/lib/themePacks.ts`. It is a list of packs, each with `id`, `title`, `description` and `text`. The text uses the same paste format, so applying it goes through the same parser.
- **UI:**
  - On the 故事 page, a 「套用主題包」 button opens a dialog listing the packs; there is one for now.
  - Choosing a pack opens the existing 貼上整本故事 dialog prefilled with the pack text. The user can edit it there, and the preview shows exactly what will be filled.
  - Nothing is applied without the user confirming in that dialog. The existing fill-empty-only and never-delete rules apply.
- **Content: 10 pages.** Scene wording leaves vehicles out, and action wording says what the vehicle looks like and where the character is. The pack text ends every scene with 「，沒有任何車輛、船、飛機或火車」.
  1. 消防車 Fire Engine. Scene: 消防局門口，車庫門關著，旁邊有消防栓. Action: 開著紅色消防車，車上有長梯子，從車窗探出頭揮手.
  2. 救護車 Ambulance. Scene: 醫院門口，有大門和紅十字招牌. Action: 站在白色救護車旁邊揮手，車身有紅十字.
  3. 警車 Police Car. Scene: 警察局門口，有旗桿和台階. Action: 坐在白色和藍色的警車駕駛座上，車頂有紅藍警燈.
  4. 公車 Bus. Scene: 公車站，有站牌和候車亭. Action: 坐在黃色公車的駕駛座上揮手，畫出整台公車的外觀.
  5. 計程車 Taxi. Scene: 城市馬路，有紅綠燈和斑馬線. Action: 坐在黃色計程車的駕駛座上，車頂有計程車燈.
  6. 火車 Train. Scene: 火車站月台，有時鐘和長椅. Action: 坐在火車頭上開心地笑.
  7. 飛機 Airplane. Scene: 機場，有塔台和跑道. Action: 坐在小飛機的駕駛座上.
  8. 帆船 Sailboat. Scene: 海邊碼頭，有燈塔和海鷗. Action: 坐在小帆船上划船.
  9. 腳踏車 Bicycle. Scene: 公園的腳踏車道，有大樹和長椅. Action: 騎著腳踏車.
  10. 直升機 Helicopter. Scene: 山上的草地，有大石頭和小花. Action: 坐在直升機裡，從窗戶揮手，畫出整台直升機的外觀.
- The 中文 and 英文 lines are the vehicle names above, e.g. 「消防車」 and 「Fire Engine」.
- Taxi and helicopter were not in the spike. The bus and police wording comes from the spike's findings. Acceptance runs the whole pack.

### 4.6 Details settled after the builder check (author's calls)
- **Scaling the art above the band:**
  - Resample the 1-bit art as grayscale (LANCZOS) and re-threshold at 128.
  - If the measured stroke width falls below 10 px, dilate by 1 px so lines stay about 10–12 px.
  - A test asserts that the stroke width after scaling is ≥ 10 px and that the gap count doesn't rise compared with the unscaled art.
  - QA keeps judging the unscaled line art, so captions don't reset QA.
- **Print-shop mode:**
  - The art is scaled inside the content area, so the bleed margins become white.
  - Coloring pages are white paper, so this is acceptable. The band and the captions stay inside the safe box.
- **Shared views:**
  - The caption text is not needed separately, because the image carries it.
  - A thinner outline in the 1600 px preview is fine.
  - A missing font becomes a 404 for viewers, as for any other export failure there. This is acceptable because it only happens when the server is misconfigured, and preflight reports it.
- **Clearing a caption:**
  - `""` or whitespace clears the caption to null, matching how the existing prompt fields behave.
  - An explicit null also clears it, if `PageUpdate` already distinguishes unset from null. Follow the existing pattern.
  - The editor's save queue carries the new fields like the scene and action fields.
- **Paste labels:**
  - The labels are only `中文`, `英文` and `English`. `文字` is dropped, so it can't clash with unlabelled text.
  - Caption lines may appear anywhere inside a page block after its heading, in any order, but not on the heading line itself.
  - Limits count Unicode code points: `Array.from(s).length` in TypeScript and `len()` in Python.
- **Applying the pack to a book that already has pages:** existing import rules apply.
  - New pages are created for missing positions.
  - Each field is filled only where it is empty, and nothing is deleted.
  - The pack is meant for a new book. The dialog's preview already shows what will be filled.
- **Band ratio:** a fixed 15% for every paper size, since all supported papers are portrait.
- **T3 applies only to new pose generations.** Each generation already stores its full prompt, so old ones keep theirs.

## 5. Success criteria
- **Unit tests:**
  - migration 0010 up and down;
  - caption validation and limits;
  - captions saved through `PageUpdate` keep the page's status;
  - `composed_page`:
    - no captions → pixel-identical to today;
    - captions → a band at the bottom, the art scaled into the area above, and in print-shop mode the band inside the safe box;
    - a long line shrinks to fit;
    - hollow letters (the inside of a glyph is white);
  - a missing font → 409 on export only when captions exist, plus a preflight entry;
  - parser labels, columns and limits;
  - the theme pack text parses to 10 pages with captions and no problems;
  - the T3 constant.
- **e2e:**
  - apply the 交通工具 pack and import → 10 pages with captions;
  - edit a caption on a story row;
  - the exported PDF still downloads;
  - 390 px has no horizontal overflow on 故事 with the caption inputs.
- **Real-GPU acceptance:**
  - Ask the user first, check the GPU is idle, and run `POST /free` afterwards.
  - Run the vehicles pack with one character, the bunny, through the whole flow: background, pose, compose, line fixing, PDF.
  - Score each page: the vehicle is recognisable and correct, the character stays consistent, there are no vehicles in the background, and there is no garbled text.
  - Record gaps and tiny regions, and check that the hollow captions print clearly and can be coloured.
  - Gate: at least 8 of 10 pages usable without changing the text. Fix the pack wording for any failures and record the changes.
  - Write the results to `experiments/results/phase11_vehicles_acceptance_<ts>/NOTES.md`.

## 6. Calls made by the author (not decided by the user)
- Caption limits are 12 and 28 characters, the band is 15%, and the size ratios are as above.
- The pack has 10 pages with the scenes and actions above.
- Captions don't reset QA.
- The editor shows a text preview plus a link to `print.png`, not a live PDF render.
- The font is copied into the repo (about 12 MB) rather than downloaded at build time.
