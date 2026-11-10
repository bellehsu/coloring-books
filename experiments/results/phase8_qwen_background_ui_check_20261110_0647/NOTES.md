# Phase 8 real-GPU acceptance: background style 簡潔 (Qwen) / 細緻 (SDXL), 2026-11-10

## Setup
- ComfyUI 0.36.0 @ 8188, RTX 5080. Before starting, GPU usage was 0% and `/queue` was empty (models had been unloaded beforehand). After the run, models were unloaded with `POST /free`.
- Branch `phase8-qwen-background` (6813924). A separate backend ran on 8020 with scratch storage, and the frontend on 3020. The user's dev.sh and `storage/app.db` were not touched.
- The script `accept_run.mjs` was first dry-run against the fake backend, where the full flow passed. It then ran against the real GPU.
- Flow, the same as Phase 7:
  - create the book and the character;
  - write all three scenes first;
  - 「產生所有背景」 using the default 簡潔 style;
  - for each page: precise pose → compose → line art plus autofix → QA pass.
  - Then a 4th page: upload a composition sketch via the API (`processed/p8_sketch.png`), reload, and press 「產生背景」.
- Same scenes and actions as phases 6 and 7 (park/揮手, kitchen/拿著東西, beach/跳起來).

## Results

| | Phase 8 (簡潔 Qwen background) | Phase 7 (SDXL background) |
|---|---|---|
| Confirmation | 「會為 3 頁產生背景（簡潔）」 | 「會為 3 頁產生背景」 |
| Background engine time | 29.6, 23.3, 18.2 s (first includes loading Qwen) | 19.5, 5.4, 5.7 s |
| All three backgrounds done | 76.3 s | 33.1 s |
| Precise pose engine time | **24.9, 23.2, 22.5 s** (Qwen already loaded) | 33.6, 33.8, 26.4 s (switching from SDXL) |
| Per page, choosing background to pass | **37–38 s** | 39–48 s |
| Gaps before autofix | **37 / 19 / 11** | 127 / 33 / 61 |
| QA 「太多過小的區域」 warning | **none on all 3 pages** | pages 1 and 2 |
| Three pages total (from 產生所有背景 to page 3 passed) | 188.5 s | 166.0 s |

- Every page passed, with 0 gaps after autofix. The PDF exported (`%PDF`, 83 KB).
- At 390 px width, horizontal overflow was 0 px.
- request_json records `background_style=simple`, `workflow_name=qwen_background`, and one random variation per page (left side / right side / right side).
- The poses use the same Qwen family, so the whole book did not switch model family.
- **Total time is about 22 s slower than Phase 7.** A Qwen background takes about 20 s versus about 6 s for SDXL. Poses are faster because there is no model switch, but that does not cover the difference.
- The gain is line quality: about 4× fewer gaps, and no tiny-region warning.
- Visually, the three backgrounds are clean, with clear blank areas, and the character composites naturally (`processed/sheet.jpg`).
- The "right side" variation leaves the left half of the beach page blank. The character sits there, so it reads fine.

### Page 4 (with sketch)
- 簡潔 is disabled and 細緻 is selected, with the note 「有構圖草圖時用細緻，才會照草圖構圖」.
- 「產生背景」 sent `{"style":"simple"}` (the user's choice). The backend answered `background_style: "detailed"`, used `workflow_name=sdxl_background`, took 29.8 s including the switch back to SDXL, and the history badge shows 「細緻」.
- The generated image follows the sketch: a box on the left (bed), a large frame (window), and a circle at the bottom right.
- **Script defect:** the scene description for this page was never saved. The script moved focus to the 「產生所有背景」 button to trigger the save, but that button was already disabled because every page had a background, so the textarea never lost focus and the reload discarded the text. The backend log shows no PATCH for page 4. As a result this background has no scene text and is just abstract shapes guided by the sketch. This does not affect what the page was testing (switching to 細緻 when there is a sketch, and following the sketch). The product code was not changed.

## Conclusion
- The 簡潔/細緻 switch, the forced 細緻 when a sketch is present, provenance records, badges and the confirmation text all behave as specified.
- 簡潔 trades about 22 s more per book (3 pages) for clearly cleaner line art. Users who want speed can switch to 細緻.
