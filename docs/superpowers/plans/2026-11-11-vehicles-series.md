# Vehicles Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pages carry Chinese and English captions, printed as hollow letters under the art. A built-in 「交通工具」 theme pack fills a book in one step, and the pose template draws vehicles on purpose.

**Architecture:**
- Captions are two nullable page columns.
- `composed_page` is the single print path: it adds a caption band and scales the art above it, so the PDF, `print.png` and the shared image all get captions.
- Input goes through the existing paste parser (new labels), the story rows and the editor panel.
- The theme pack is frontend data in paste format, applied through the existing import dialog.

**Tech Stack:** FastAPI, SQLModel/SQLite, Alembic, Pillow, Next.js 16, React 19, TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-11-vehicles-series-design.md` (§4.1–4.6 binding; §4.6 settles the builder-check details).

## Global Constraints
- Python 3.11+. Tests: `cd <worktree> && env -u PYTHONPATH PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`. In frontend/: `npx vitest run`, `npm run lint`, `npx tsc --noEmit`, `PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e` (ports 8010/3010).
- Work only in the worktree `/ai-data/repos/coloring-books-phase11` on branch `phase11-vehicles`.
  - Never touch `/ai-data/repos/coloring-books`; the user's dev.sh runs from there.
  - Never read `scripts/dev.env`.
  - Ports 3000, 8000 and 8188 are off limits.
- Paths come from settings. The font path is `paths.caption_font`, defaulting to `assets/fonts/NotoSansTC-VF.ttf`, resolved like the other paths and overridable with `APP_PATHS__CAPTION_FONT`.
- Caption limits count code points: 12 for Chinese and 28 for English. The 422 messages are 「中文字最多 12 個字」 and 「英文字最多 28 個字元」. Missing font on export → 409 「找不到字型檔，無法印出頁面文字」.
- Band layout:
  - The band is 15% of the content height.
  - The Chinese line's centre is at 30% of the band, with glyphs about 50% of the band height. The English line's centre is at 78%, with glyphs about 30%.
  - A line wider than 90% of the content width shrinks to fit. A single line is centred at the Chinese size.
  - The font is Noto Sans TC, variation "Black". Letters are hollow: white fill and a black stroke of 7–11 px.
- Pages without captions must be pixel-identical to today. Captions never reset QA or status.
- zh-Hant UI copy exactly as the spec gives it. Local commits only, never push. Every commit ends with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`.

## Review Focus
1. **A caption-less page prints differently after this change.** That breaks the anti-goal. Task 2 pins it with a pixel-identical test.
2. **Scaled art loses stroke width or opens gaps.** Task 2 asserts stroke ≥ 10 px and that gaps don't increase.
3. **Print-shop band outside the safe box.** Task 2 asserts the band and the glyph bounding boxes are inside `safe_box_px`.
4. **Paste text using 「文字：」 or a caption on a heading line is silently mis-parsed.** Task 3 asserts that `文字：` stays unlabelled and that a heading line never yields a caption.
5. **Code-point vs UTF-16 length mismatch between the frontend counter and the backend 422.** Tasks 1 and 3 both test a string containing a non-BMP character (e.g. 「𠮷」) at the limit.

---

### Task 1: Caption fields + pose template T3 (backend)

**Files:**
- Create: `backend/migrations/versions/0010_page_captions.py`
- Modify:
  - `backend/app/models.py` (`Page.caption_zh`, `Page.caption_en`)
  - `backend/app/api/schemas.py` (`PageUpdate`, `PageOut`, caption types)
  - the page PATCH handler (`backend/app/api/pages.py`)
  - `backend/app/services/prompts.py` (`QWEN_POSE_TEMPLATE` = T3)
  - `tests/test_prompts.py`, `tests/test_migrations.py`
  - a new or existing page API test file

**Interfaces:**
- Produces:
  - `PageOut.caption_zh: str | None`, `PageOut.caption_en: str | None`
  - `PageUpdate.caption_zh` and `caption_en`, accepting a string or null. `""`, whitespace and null clear the field to null. Follow how PageUpdate already distinguishes unset fields.
  - `QWEN_POSE_TEMPLATE` exactly as spec §4.4 "New".

- [ ] **Step 1: Write failing tests.**
  - Migration 0010 upgrades and downgrades (the columns exist and are nullable).
  - PATCH: `caption_zh="消防車"` and `caption_en="Fire Engine"` are both returned.
  - Values are trimmed.
  - `""` clears to null.
  - 13 code points → 422 「中文字最多 12 個字」. A string of 12 code points that includes 「𠮷」 → 200.
  - 29 code points → 422 「英文字最多 28 個字元」.
  - A PASSED page stays PASSED after a caption change, and `selected_generation_id` is unchanged.
  - Another owner → 404, through the existing owned_* guard.
  - `test_prompts`: `QWEN_POSE_TEMPLATE` equals the spec T3 string. `"no text, no letters"` is in it, and `"even if the action mentions a place"` is not. `qwen_pose_prompt("騎著腳踏車")` starts with "Make this exact character 騎著腳踏車.".
- [ ] **Step 2:** Run the tests and confirm they fail for the right reason.
- [ ] **Step 3:** Implement. Update the generated frontend API types with `npm run gen:api` in frontend/.
- [ ] **Step 4:** Run the full backend suite, then vitest and tsc, because the types changed.
- [ ] **Step 5:** Commit `feat: page captions (zh/en) and pose template T3`.

### Task 2: Caption band in `composed_page`, font setting and preflight

**Files:**
- Create: `backend/app/services/captions.py`, `tests/test_captions.py`
- Modify:
  - `backend/app/api/export.py` (`composed_page` passes the page captions)
  - `backend/app/services/export.py` (`compose_page` gains `captions`)
  - `backend/app/config.py` (`PathSettings.caption_font`)
  - `backend/app/services/preflight.py`: the font check is reported as a separate item and never fails preflight as a whole
  - `config/settings.example.yaml`, README (one line)

**Interfaces:**
- Consumes: `Page.caption_zh/en` (Task 1); `Layout` (`content_px`, `content_offset_px`, `safe_box_px`); `LINE_W`.
- Produces:
  - `render_caption_page(ink: np.ndarray, layout: Layout, zh: str | None, en: str | None, font_path: Path) -> Image.Image`, returning the full 1-bit page.
  - `compose_page(ink, layout, captions: tuple[str | None, str | None] = (None, None), font_path: Path | None = None)`. With both captions None it behaves exactly as today.
  - `CaptionFontMissing(Exception)`, which the API maps to 409.

- [ ] **Step 1: Write failing tests.**
  - No captions → `compose_page` output is byte-identical to the current output for A4 home and A4 print-shop.
  - Captions → the bottom 15% band of the content area holds the text pixels, and no art pixels lie in the band.
  - The art's bounding box sits above the band and is horizontally centred.
  - Print-shop: the band and the text bounding boxes are inside `safe_box_px`.
  - Stroke width:
    - measured on a synthetic test art of 11 px lines, the scaled art's median stroke is ≥ 10 px;
    - `qa.evaluate` gap count on the scaled art ≤ the count on the original.
  - Hollow letters: inside the 「口」 of 「中」, or some glyph with a known counter, there are white pixels surrounded by black.
  - A 28-character English line is narrower than 90% of the content width after shrinking.
  - A single caption is centred vertically in the band.
  - A missing font with captions raises `CaptionFontMissing`, and the export route returns 409 with the spec text. A missing font without captions is fine.
  - The preflight result lists the font problem without failing the other checks.
  - `print.png` for a captioned page has the band, and so do the shared image and the shared PDF.
- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement per spec §4.2 and §4.6:
  - Load the font once per process, cached by path and size.
  - Use the variation by name "Black".
  - Scale the art: grayscale LANCZOS, then re-threshold at 128. If the measured stroke is under 10 px, dilate by 1 px.
  - Pick the stroke width between 7 and 11 px so the counters stay open, and write the chosen value in a comment.
- [ ] **Step 4:** Run the full backend suite.
- [ ] **Step 5:** Commit `feat: print bilingual hollow captions under the art`.

### Task 3: Caption input (paste parser, AI prompt, story rows, editor panel)

**Files:**
- Modify:
  - `frontend/src/lib/storyImport.ts` (+ test)
  - `frontend/src/lib/limits.ts`
  - `frontend/src/features/story/aiPrompt.ts`, `StoryRow.tsx`, `ImportDialog.tsx` (+ tests)
  - the editor's ① 構圖 panel and its save queue (`frontend/src/features/editor/…`)
  - the caption text preview under the canvas in both steps, and the 「預覽列印頁」 link (`/api/pages/{id}/print.png`, opened in a new tab, shown only when a caption exists)

**Interfaces:**
- Consumes: `PageOut.caption_zh/en`, `PageUpdate.caption_zh/en` (Task 1).
- Produces:
  - `ParsedPage` gains `captionZh?: string` and `captionEn?: string`.
  - `ImportRow.fill` gains `captionZh` and `captionEn` booleans.
  - `MAX_CAPTION_ZH = 12` and `MAX_CAPTION_EN = 28` in limits.ts.
  - `countChars(s) = Array.from(s).length`.

- [ ] **Step 1: Write failing tests.**
  - Parser:
    - Labels 「中文：」, 「英文：」 and 「English:」 (any case, full or half-width colon), anywhere in a page block after the heading and in any order, become captions.
    - 「文字：…」 is NOT a caption.
    - A heading line such as 「第1頁 中文：消防車」 yields no caption.
    - Spreadsheet columns 中文, 英文 and English work.
    - Over-limit text gives row problems with the spec messages.
    - A 12-code-point string that includes 「𠮷」 is accepted.
    - Captions are filled only where empty.
  - AI prompt: it contains the two optional caption lines exactly as in spec §4.3.
  - StoryRow:
    - the two inputs 「中文字」 and 「英文字」 show remaining counts and save through the existing page-update path;
    - clearing saves an empty value.
  - Editor:
    - the ① panel has the same inputs;
    - the caption preview text shows in both steps;
    - the 「預覽列印頁」 link appears only when a caption exists and points at `print.png`.
  - e2e: edit a caption on a story row, reload, and it persists. At 390 px the 故事 page has 0 px horizontal overflow.
- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement. Reuse the existing debounced page save and its `pendingWrites` handling.
- [ ] **Step 4:** Run vitest, lint, tsc, e2e and the backend suite.
- [ ] **Step 5:** Commit `feat: caption inputs, paste labels and print preview link`.

### Task 4: Theme pack 「交通工具」

**Files:**
- Create: `frontend/src/lib/themePacks.ts` (+ test), `frontend/src/features/story/ThemePackDialog.tsx` (+ test), `frontend/e2e/themepack.spec.ts`
- Modify: `frontend/src/features/story/StoryPage.tsx` (the 「套用主題包」 button), `ImportDialog.tsx` (accepts initial text)

**Interfaces:**
- Consumes: `parseStory` (or whatever the parser export is called) with captions (Task 3).
- Produces:
  - `THEME_PACKS: ThemePack[]`, where `ThemePack = {id: string; title: string; description: string; text: string}`.
  - `ImportDialog` takes an `initialText` prop.

- [ ] **Step 1: Write failing tests.**
  - `THEME_PACKS` has one pack with `id: "vehicles"` and `title: "交通工具"`.
  - Its text parses to exactly 10 pages with no problems, in the order of spec §4.5.
  - Every scene ends with 「，沒有任何車輛、船、飛機或火車」.
  - The captions match the list, e.g. page 1 is 「消防車」 / 「Fire Engine」 and page 10 is 「直升機」 / 「Helicopter」.
  - All scenes and actions are within the existing limits.
  - Clicking 「套用主題包」 → the dialog lists the pack → choosing it opens 貼上整本故事 with that text, and its preview shows 10 pages. Nothing is written until the user confirms.
  - e2e on a new book: apply the pack, then import → 10 pages, each with a scene, an action and both captions.
- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement. The pack text uses the spec's scenes, actions and captions verbatim.
- [ ] **Step 4:** Run vitest, lint, tsc and e2e.
- [ ] **Step 5:** Commit `feat: built-in 交通工具 theme pack`.

### Task 5: Real-GPU acceptance (the controller runs this; ask the user first)

- [ ] Check that the GPU is idle and tell the user.
- [ ] Use a copy-free setup: backend on 8001 with a fresh local DB and local storage in the worktree, real ComfyUI 8188, frontend on 3001.
- [ ] Run the whole flow with one bunny book:
  - create the character, apply the pack, and import;
  - 產生全部素材;
  - for each page, compose, run 自動補線, mark it 對, and complete it;
  - export the PDF.
- [ ] Score each page: vehicle correct and recognisable, character consistent, no vehicle in the background, no garbled text. Record gaps and tiny regions, and check that the captions are hollow and clear.
  - Gate: at least 8 of 10 pages usable without changing the text.
  - Change the pack wording for any failures, and record the changes.
- [ ] Run `POST /free` and notify the user. Write `experiments/results/phase11_vehicles_acceptance_<ts>/NOTES.md` and commit.
