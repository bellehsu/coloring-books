# Task 18 acceptance: code review fixes (master 9bd5505), real ComfyUI, browser walkthrough

Date 2026-11-12, ~16:30 Taipei. Acceptance servers: backend :8001 + Next dev :3001 from master 9bd5505, separate DB/storage under `storage/` here (local backend), real ComfyUI 0.36.0 on :8188 (Qwen-Image-Edit-2511 Q4 GGUF, RTX 5080). Driven by Playwright (`walk.mjs` in the session scratchpad; log in `walk.log`). Screenshots in `shots/`, PDF in `book.pdf`.

## Result: PASS (all steps), with one pre-existing bug found

| Step | Result | Time |
|---|---|---|
| New book (title + A4 home) → auto-created, URL → /books/1/setup | ✅ | 0 s |
| Character 「一隻圓圓的小白兔，長耳朵，圍著紅色圍巾」 generated + chosen | ✅ | 36 s |
| Paste 3-page story → 「讀到 3 段」 | ✅ (but see bug) | — |
| 開始做這本書 → B → 「整本做好了！」 | ✅ 2 pages 可以印 | 135 s |
| C page 1: 15× ArrowRight (held key) → placement changed, recomposed | ✅ | — |
| C: 英文 off → saved (caption_show_en=false) | ✅ | — |
| C: 復原 #1 → 英文 back on; 復原 #2 → placement back to default | ✅ held arrow key = ONE undo step (Review Focus 5) | — |
| 進階編輯: 換一張角色動作 → new pose auto-swapped (3 → 9) + recomposed | ✅ (E2) | 36 s |
| 回到處理這一頁 → C 復原 → old pose 3 restored | ✅ E2 undoable | 3 s |
| 印出這本書 → 下載 PDF (%PDF, 42 KB) | ✅ | — |

Browser errors: one 409 on `GET /api/pages/1/qa` while the page was mid-recompose (expected; C shows the retry state since Task 9).

## Bug found (pre-existing, not from this branch)

**One-line captions 「中文：公園　英文：Park」 are not split.** The story textarea's own placeholder shows this one-line form, but `parseStory` (frontend/src/lib/storyImport.ts) puts the whole rest of the line into `captionZh` → printed caption 「公園 英文：Park」; page 3 「麵包店 英文：Bakery」 (14 chars) exceeded the 12-char limit and was silently not imported (only 2 of 3 pages created even though the preview said 「讀到 3 段」).

## Other observations
- Overview screenshot `04b` shows page 2's thumbnail empty — taken the instant 「整本做好了！」 appeared; page 2's print (`print_page2.png`) is fine, so this is most likely image loading timing, not a bug.
- First walkthrough attempt failed in the test harness only: Next dev blocks `/_next` dev resources for origin 127.0.0.1 unless `DEV_ALLOWED_ORIGINS` is set (dev.sh sets it; the acceptance start script now does too).
- GPU: `POST /free` after the run.
