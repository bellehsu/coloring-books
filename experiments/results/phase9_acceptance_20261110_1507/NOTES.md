# Phase 9 real-GPU acceptance: all-Qwen + 故事 step + two-step editor (2026-11-10)

> The session that wrote the original plan stopped partway through. This acceptance was run by the session that took over (see the ledger TAKEOVER section).

## Setup
- ComfyUI 0.36.0 @ 8188, RTX 5080. The GPU was idle and `/queue` was empty before the run. Models were unloaded with `POST /free` afterwards.
- Branch `phase9-all-qwen` (final fix round 5f705e4).
- `storage/` was copied in full to `storage-accept/`, and the backend ran on that copy on port 8001.
  - At startup it ran migration 0005 and cancelled queued SDXL jobs.
  - The frontend ran on 3001.
  - The real `storage/app.db` was not touched. `storage-accept` was deleted after the run.
- Preflight listed only `qwen_background`, `qwen_character` and `qwen_edit_pose_cutout`, all OK.
- The script `accept_run.mjs` first ran against the fake backend on a copy of the real DB, where the whole flow passed. It then ran on the real GPU. Screenshots, the PDF, `results.json` and `sheet.jpg` are in `processed/` (not version-controlled).

## Results

### Old data (Step 2)
- Both existing books, 1 and 2, open.
- `export.pdf?include=all` returned 200 for both.
- Book 1's legacy pages 1 and 2 open in the editor, and lineart returned 200 for both.

### New book, all inputs in Chinese (Step 3)

| Step | Time | Notes |
|---|---|---|
| Character: 「一隻圓圓的小白兔，長耳朵，圍著紅色圍巾」 → 產生角色 | 26.4 s (engine 22.3 s, includes model load) | 1 image (user decision) |
| 再產生一張 | 20.4 s (engine 16.1 s) | Variation phrase differs from the last one (Chubby round → Big head) |
| 故事: paste 3 pages, then set page 3 to 上傳圖片 | Upload 201 (0.2 s) | Preview splits the pages correctly |
| 「產生全部素材（還有 5 張）」 | **107 s** for all of them | Background 15.8 / 15.9 s; pose 22.8 / 22.9 / 21.2 s; auto-selected into the empty slots |
| Each page: open → auto-compose | 1.4 s | ✓ 線稿已自動更新 |
| Each page: drag → recompose | 1.7–3.8 s | |
| ② 補線: gaps → 自動補線 | 19 / 16 / 5 gaps → all 0 (3 s each) | Page 3 is the uploaded illustration |
| 對 → 完成這一頁 ✓ | All 3 pages complete | No gaps left, so no confirm dialog appeared |
| Each page, from opening to complete | 9.7 / 9.6 / 11.6 s | |
| Export PDF | `%PDF`, 69 KB | |

- **Whole book.** From pressing 產生全部素材 to all materials being ready took about 107 s, and the three pages took about 31 s more. Comparison:
  - Phase 8: 3 pages took 188.5 s, including waiting for poses one page at a time.
  - Phase 7: 166 s.
  - Phase 9 generates all materials in one batch, so the remaining work is only compose, line fixing and completion.
- **Visual check** (`processed/sheet.jpg`):
  - The bunny is consistent on all three pages, with the right actions: waving, holding an apple, jumping.
  - No scarf or other accessory was added.
  - The Chinese scenes drew the right content: a sunny park with a bench, a kitchen with a teapot.
- **390 px width.** Horizontal overflow is 0 px for 構圖, 補線 and 故事.

### Redoing a legacy page (Step 3b)
- Book 1 page 1 (a copy) started with its selection on a PAGE generation.
- I called the API to make a pose (`開心地揮手`) and to upload a background, then selected the uploaded background.
- Old whole-page pages don't auto-compose when opened; this is by design. I nudged the character once with the arrow keys, and it then composed automatically.
- The selection became a **COMPOSITE**, replacing the old whole-page image. This took 29.6 s, including generating the pose.

## Observations
- The variation phrases rotate as designed: the two character images and the three backgrounds each used a different phrase.
- Each step produced 2 COMPOSITE rows: one from auto-compose on open, one after the drag. This is expected.
- Not covered in this acceptance: the confirm dialog when gaps remain (all three pages were fully fixed by auto-fix), and whether hole filling turns a hands-on-hips loop white (none of these three actions produced one).
