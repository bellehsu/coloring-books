# Phase 9: all-Qwen generation and a simpler editor — design

Date: 2026-11-10. Comes after Phase 8 (background style switch 簡潔 Qwen / 細緻 SDXL) is merged.
Roadmap agreed with the user: Phase 8 → **this phase (收斂介面)** → storage changes → cloud (Modal serverless GPU).

## 1. Why

The user wants the tool close to a product that 5–10 friends and parents can try, eventually on a cloud GPU
that costs nothing when idle. Two problems stand in the way today:

1. **Prompts must accept Chinese.** The testers will type in Chinese. Only the Qwen paths understand it
   (Qwen-Image-Edit's text encoder is Qwen2.5-VL). Every SDXL path (character creation `sdxl_text2img`,
   fast pose `sdxl_pose_cutout`, detailed background `sdxl_background`, legacy whole page
   `route_a_sdxl_ipadapter_cn`) uses CLIP, which does not.
2. **Too many choices for a non-technical user.** Pose 精準/快速, background 簡潔/細緻, a sketch board,
   ControlNet strength, and the legacy whole-page flow all add switches and code paths.

Going all-Qwen fixes both. It also helps the later cloud phase: one model family (about 23 GB instead of
about 37 GB) means faster cold starts, and SDXL and Qwen never need to be swapped in VRAM.

User decisions (2026-11-10):
- The page flow is **選擇角色 → 背景 → 角色動作 → 合成 → QA（自己補線）**.
- **Each book creates its own new character** (as today; no character library).
- **The sketch board is removed.**
- **Backgrounds can be uploaded**, and an uploaded image is used **directly as the page background** for
  compositing (not as a reference that guides generation).
- **All generation moves to Qwen, gated by an experiment first.** If the experiment passes, SDXL is removed.
- **The legacy whole-page flow is removed** (it only runs on SDXL), but **pages made with it can still be
  redone with the new flow** (§4.6).

UI/UX decisions (2026-11-10, after reviewing the mockup https://claude.ai/artifact/LkcDsyxEM5VXAmUKPinEyB):
- **New book-level step 「故事」**: the book flow becomes **角色 → 故事 → 頁面 → 匯出**. In 故事 the user
  writes every page's scene and action (or picks 上傳圖片 for the background), then presses
  「產生全部素材」 once (§4.8).
- **Each page has only two steps, 「① 構圖」 and 「② 補線」** (§4.5). 構圖 holds background, pose and
  placement on one screen.
- **Auto-compose**: no 「確認合成」 button. Releasing a drag or resize composes automatically and refreshes
  the line art (§4.5).
- **補線 and QA are merged.** 「角色看起來對嗎？」 is one tap. 「完成這一頁」 with gaps left only asks for
  confirmation once, with no reason to type (§4.5).

## 2. Goals, non-goals, anti-goals

**Goals**
- Character, background and pose prompts accept Chinese (English still works).
- Character creation runs on Qwen.
- A page background is either AI-generated (Qwen) or uploaded.
- The editor has no sketch board, no pose mode switch, no background style switch, no ControlNet strength.
- SDXL workflows, the ControlNet / IP-Adapter path and the legacy whole-page flow are gone from the live app.
- Preflight only checks what the Qwen workflows need.
- Every existing book and generation can still be opened, viewed and exported.

**Non-goals**
- Storage changes (owner field, image bytes instead of paths, backups): the next phase.
- Cloud deployment, invite links, usage caps: later phases.
- A character library shared across books.
- Translating prompts (Qwen reads Chinese directly).
- Deleting model files from disk. Unused SDXL models stay where they are; the user can remove them.

**Anti-goals**
- SDXL must not be removed before the experiment (§3) passes.
- Old data must not be lost or become unopenable. No destructive DB migration.
- Uploads must never be silently distorted (stretched) to fit the page.
- The pose prompt must not be tied to one test character (see §4.4).

## 3. Experiment gate (do first, on the real GPU)

A spike covering much of E1/E2 was already started in another session
(`experiments/results/phase9_qwen_character_zh_20261110_1502/`, uncommitted at the time of writing). Reuse its
results where they meet the criteria below, and run only what is missing.

Ask the user before running: the GPU is sometimes lent to other tools. Unload models (`POST /free`) afterwards.
Record everything in `experiments/results/phase9_all_qwen_<timestamp>/` (consistency scores, line-art
metrics, times, failures), as CLAUDE.md requires.

**E1 — Qwen character creation.** Use 6 Chinese character descriptions of different kinds: at least one
animal, one child, one vehicle or object character, plus one with clothing or accessories. Run 4 seeds each,
with the same graph as `qwen_background` (no image1). Calibrate a character prompt template, plus variation
phrases if the 4 candidates look the same (the Phase 7 spike found Qwen seeds give near-identical layouts).

It passes when all of these hold:
- each description gets at least 2 usable candidates out of 4: a single full-body character, centred,
  plain white background, flat colours, thick outlines, nothing cut off;
- the 4 candidates of a description are visibly different, so that choosing among them means something;
- a precise pose made from the chosen candidate keeps the character recognisable. Score consistency 1–5
  as in Phase 6; the mean must be at least 3.5.
- **regression case:** the existing Phase 6 bunny (book 2's character) also runs through the generalised
  pose template (§4.4) and must score at least 4.0 (Phase 6 scored 4.1 with the bunny-specific wording).

**E2 — Chinese prompts.** Use 6 scenes and 6 actions, each written in Chinese and in English. Run
backgrounds through `qwen_background` and poses through `qwen_edit_pose_cutout`, with 2 seeds each.

It passes when all of these hold:
- the Chinese results show the requested content in at least 5 of 6 cases. If the English baseline
  also misses a case, that case is a prompt problem, not a Chinese one: it doesn't count against Chinese,
  and the bar becomes "Chinese is no worse than English";
- line-art metrics stay within 20% of the English runs (gaps, tiny regions);
- backgrounds contain no people or animals.

Also try two prompt shapes: the user's Chinese text inside the current English template, and a fully
Chinese template. Adopt whichever scores better; if they tie, keep the English template.

**If either experiment fails:** stop, write up the results, and bring the user a decision before
removing anything. Do not choose a fallback (keeping SDXL for characters, adding translation, …) on your own.

## 4. Behaviour after the gate passes

### 4.1 Character creation (選擇角色)
- New workflow `comfy_workflows/qwen_character/`: the same graph as `qwen_background` (no image1), with
  parameters `prompt`, `seed`, `width`, `height`. Check node classes and inputs against the running
  ComfyUI's `/object_info`.
- `prompts.qwen_character_prompt(description, variation=None)` uses the E1 wording. There is no negative
  prompt (cfg 1), so `negative_prompt` is stored as "".
- Size: E1 decides it (a portrait size that is a multiple of 16, near today's `CHARACTER_SIZE`).
- The candidates API, the count and the CharacterStudio flow are unchanged; only the engine behind them
  changes. Variation phrases (if E1 needs them) are recorded in request_json as `variation`, like backgrounds.

### 4.2 Background (背景)
**Generate.** Always `qwen_background`. The style switch from Phase 8 is removed, along with the sketch rule.
`POST /pages/{id}/backgrounds` and the batch endpoint ignore any `style` field, so old clients don't break,
and always record `background_style: "simple"`.

**Upload.** New endpoint `POST /pages/{id}/backgrounds/upload` (multipart field `file`):
- Validate with `storage.normalize_png` (PNG/JPEG/WebP, 10 MB, the existing size rules) → 400 with the
  Chinese reason on failure.
- Fit to the book's `generation_size` with a **centred cover crop**: scale to fill, crop the overflow, no
  stretching.
- Create a `Generation` with kind BACKGROUND, status SUCCEEDED, `engine: "upload"`, `workflow_name` null,
  seed 0, and `elapsed_s` set to the processing time. `params_json` records the original filename, the
  original size and the crop box. `user_text` is empty.
- Make it the page's `background_generation_id`. This goes through the same code path as choosing a
  background from history, so the downstream state is reset the same way.
- It shows up in the history tray like any other background, with a 「上傳」 badge, and can be re-selected
  or discarded.

**Frontend.** Upload is offered in two places: 故事's 「上傳圖片」 (§4.8) and the 構圖 背景 card's
「上傳背景圖」 (§4.5). Both show the hint 「線條簡單的插畫效果最好；照片擷取的線稿可能很亂，需要多補線。」.
The editor's 「產生所有背景」 is replaced by 故事's 「產生全部素材」.

### 4.3 Removed
- SketchCanvas, sketchSaver, referenceSketch, the 構圖草圖 section and the ControlNet strength slider.
- Endpoints `PUT/DELETE/GET /pages/{id}/sketch` and `POST /books/{id}/reference-sketch` are removed.
- The `page.sketch_path` and `page.controlnet_strength` columns stay in the DB, unused. No migration drops
  them. Sketch files on disk are left alone.

### 4.4 Pose (角色動作)
- Always precise (`qwen_edit_pose_cutout`). The 精準/快速 switch, `poseMode.ts`, `/api/system/pose-modes`
  and `pose_modes.py` are removed; the API ignores any `pose_mode` field.
- **The template must be generalised.** `QWEN_POSE_TEMPLATE` currently says "scarf and ears unchanged",
  which was tuned on the Phase 6 test rabbit. Replace it with wording that holds for any character, and
  validate it in E1 (third criterion).
### 4.5 Page editor: two steps
The mockup's 「③ 頁面：構圖」, 「③ 頁面：補線與完成」 and 「手機：構圖」 boards are the reference layout.
The page list on the left and the book bar stay as today. The centre tabs 背景／角色／合成／線稿與修線／QA 檢查
become two: **「① 構圖」** and **「② 補線」**.

**① 構圖**
- The stage always shows the selected background with the cut-out pose on top. The user drags to move and
  resizes with the corner handle. 「水平翻轉」 and 「重設位置」 remain.
- The right panel has two cards:
  - **背景**: the history thumbnails (radio), 「換一張」 (Qwen, using the page's scene text),
    「上傳背景圖」 (§4.2), and a collapsible scene text editor.
  - **角色動作**: the history thumbnails, 「換一張」, and a collapsible action text editor plus the
    existing quick chips.
  The 「上傳」 badge shows on uploaded backgrounds.
- **Auto-compose.** When a drag or resize ends, or after flip, reset, or picking another background or pose,
  save the placement and request the composite. Debounce so that a burst of changes makes one composite;
  reuse today's `placementSaver` / `pendingWrites`. A small status reads 「✓ 線稿已自動更新」 or
  「更新中…」. The 「確認合成」 button and the old compose step in the checklist go away.
  As today, a new composite drops line fixes made on the previous one. Before replacing a composite that has
  line edits, the UI asks once: 「移動角色會清掉這頁補過的線，要繼續嗎？」. This is my call; the user
  accepted auto-compose but not silent loss of manual work.
- When the pose or background is missing (still queued or generating), the stage shows what is there plus a
  status, and 構圖 waits. The user can switch pages meanwhile.
- 「下一步：補線 →」 switches to ② 補線.

**② 補線**
- The stage is today's LineFixCanvas: tools 補線, 擦除, 復原, zoom. The 自動補線 button moves to the panel.
- Gaps found by QA are drawn as orange circles on the stage, using the existing QA overlay data.
- The right panel has three cards:
  1. **Check**: 「還有 N 個缺口」 with one plain-language sentence and 「自動補線」, or 「✓ 線條都接好了」.
     Warnings that are not gaps stay as today's list, in smaller text.
  2. **線條多寡**: 簡單／標準／細緻 (today's 細緻度). The line-width slider moves under a collapsed
     「進階：線寬」.
  3. **「角色看起來對嗎？」**: 「對」 records `consistent`. 「不太像，換一張」 records `inconsistent` and
     switches to ① 構圖.
- **「完成這一頁 ✓」** passes the page.
  - It needs the character answered 「對」, as today.
  - With gaps left, it opens one confirmation dialog, 「還有 N 個缺口，小朋友塗色時顏色可能漏出去。確定完成嗎？」,
    with no text field. The backend then stores `forced_pass_reason` automatically as
    「使用者確認完成（N 個缺口）」. The pass API accepts a missing reason and fills it in; today's
    required-reason rule is relaxed.
  - 「取消完成」 (today's unpass) stays available on a finished page.
- The checklist card on the right (本頁進度) shrinks to the two steps plus 完成.

**Phone width**: the page list becomes a 「第 N 頁 / M ▾」 dropdown at the top, and the two 構圖 cards become
compact cards under the stage that expand on tap. The stage stays usable for dragging.

Uploaded backgrounds go through the same composite → xdog → line post-processing path, with no new rules.

### 4.6 Legacy whole-page flow
- Creation endpoints are removed: `POST /pages/{id}/candidates` and `POST /pages/{id}/select`.
- Legacy pages (pages whose `selected_generation_id` is a PAGE generation; today book 1 pages 1–2) keep
  **today's behaviour**: they open, show their line art, allow line fixing, QA and export, **and can be
  redone with the new flow** (user decision 2026-11-10). Generating or uploading a background, making a pose
  and composing all work on them. The old PAGE image stays selected until a new composite replaces it
  (`_pick` in `api/compose.py`, and the 「舊流程頁面選了新素材」 state in `checklist.ts`). Uploading a
  background counts as "new material" exactly like a generated one. The batch endpoint keeps skipping
  legacy pages as today. No new read-only note or 409s.

### 4.7 Engine, queue, workflows, preflight
- `ComfyEngine` keeps three routes: CHARACTER → `qwen_character`, BACKGROUND → `qwen_background`,
  POSE → `qwen_edit_pose_cutout`. A queued job of any other kind, or one that asks for an SDXL route, fails
  with a clear error 「這種生成已停用」. It never silently changes model.
- On startup, a one-off data step cancels any QUEUED job that would need SDXL. In practice there should be none.
- Queue grouping (`scheduling.py`) is removed and the queue runs oldest first. `max_wait_s` and
  `last_family` go with it, including their settings keys.
- Move `sdxl_text2img`, `sdxl_background`, `sdxl_pose_cutout` and `route_a_sdxl_ipadapter_cn` to
  `archive/comfy_workflows/`. Preflight does not scan that folder, so the IP-Adapter, ControlNet and
  SDXL checks disappear.
- `GenerationOut` keeps reporting `pose_mode` and `background_style` for old rows, so history badges on
  old generations still render. New rows always say precise / simple, so the frontend hides those badges and
  shows only 「上傳」 for uploads.

### 4.8 Book step 「故事」 (new)
- New route `/books/{id}/story`, between 角色 and 頁面 in the stepper: **角色 → 故事 → 頁面 N/M → 匯出**.
  It is reachable once the character is set. After the user picks a character, CharacterStudio's main button
  becomes 「用這個角色，開始寫故事 →」.
- One row per page, in page order, with 「＋ 新增一頁」. Reordering stays in the editor's page list.
  - **背景**: segmented 「AI 產生」／「上傳圖片」. AI shows the scene textarea (`page.scene_prompt`).
    Upload shows a drop zone or file picker that calls §4.2's upload endpoint immediately, with the upload hint.
  - **角色在做什麼**: a textarea plus the existing quick chips. Store it on the page as a new nullable column
    `page.action_prompt` (additive migration). Today the action only lives on each pose generation's
    `user_text`. When the column is empty, the 構圖 action editor pre-fills from the latest pose's
    `user_text`.
  - **Status thumbnails** for the page's current background and pose: image, 「排隊中」, 「生成中 N%」
    or 「失敗，重試」.
- Text saves on blur and debounce, like today's scene text.
- **「產生全部素材（還有 K 張）」** queues, for every page:
  - a background, if it has a non-empty scene, no selected background, and is not set to upload;
  - a pose, if it has a non-empty action and no selected pose.
  K is that count, and the button is disabled at 0. Legacy pages and pages with jobs already queued are
  skipped, using Phase 7's skip rules and messages.
  - The backend gets a new `POST /books/{id}/materials/batch`. It reuses the background batch logic and adds
    poses. It returns per-page results: queued ids and skip reasons.
  - When a background or pose job succeeds and its page has nothing selected for that slot, the worker
    selects it automatically. It uses the same code path as `POST /pages/{id}/background` and `/pose`, so a
    finished page is ready in 構圖 without a click. Today selection is always an explicit call. A slot that is
    already selected is never replaced automatically.
- A status banner explains progress and says the user can go to 頁面 before everything finishes.

**Paste import 「貼上整本故事」** (user decision 2026-11-10: the user writes the storyboard elsewhere,
e.g. ChatGPT or Claude, and pastes it in; the app has no language model of its own).
- A button on 故事 opens a dialog with a large textarea. Parsing is pure, client-side and unit-tested. It
  needs no backend endpoint beyond the existing page create and patch calls.
- Two formats are accepted, detected automatically:
  1. **Labelled blocks.** A page heading starts a block: 「第N頁」, 「第 N 頁」, 「N.」, 「N、」, 「P N」 or
     「Page N」. Inside a block, 「場景：」 or 「背景：」 is the scene and 「動作：」 or 「角色：」 is the
     action. Full-width and half-width colons both work. Blocks without headings, separated by blank lines,
     count as consecutive pages.
  2. **Pasted spreadsheet rows**: tab-separated, one row per page, first column scene, second action. A
     header row containing 場景 or 背景 is skipped.
- Page N in the paste maps to the book's page at position N.
- A **preview table** (頁, 場景, 動作, 結果) is shown before anything is written:
  - Unparseable segments are flagged in red with the reason, and are not applied.
  - The limit is 40 pages per paste; anything past it is flagged.
  - Scene and action are limited to the existing field lengths, so over-long text is flagged, not cut.
- **Apply rule: fill empty fields only, never overwrite.** A page whose scene or action already has text keeps
  it, and the preview marks it 「已有內容，略過」 per field. Pages with generated or uploaded material are
  affected the same way: only their empty text fields are filled.
- When the paste has more pages than the book, the missing pages are created at the end. Pages are never
  deleted.
- 「複製給 AI 的指令」 copies a prompt template to the clipboard. It asks an assistant to turn a story into N
  pages in format 1, with one short scene per page that has **no people or animals in it** (the character is
  composited separately) and one short action per page. The text lives in a frontend constant, so it can be
  tuned without a backend change.

### 4.9 GPU status pill
The app bar shows one pill:
- 「GPU 閒置」 when the queue is empty;
- 「生成中 n/m · 剩約 X 分鐘」 while jobs run, with the estimate from recent `elapsed_s` per kind;
- 「GPU 就緒」 for a short while after the last job.

It is built from `/api/system/queue`. 「GPU 啟動中」 and 「不計費」 wording belong to the cloud phase; do not
show them locally.

### 4.10 Docs
- CLAUDE.md 生成路線: one paragraph replaces the Route C / Phase 6 / Phase 8 lines. Generation is all
  Qwen-Image-Edit-2511. SDXL, IP-Adapter and ControlNet are retired (workflows in `archive/`). Keep the
  history pointers to the old experiments.
- README: the models list (only Qwen files plus BiRefNet) and the API table.

## 5. Success criteria
- Unit tests, with ComfyUI mocked:
  - engine routing for the three kinds, and rejection of retired routes;
  - the Qwen character prompt;
  - the upload endpoint: formats, too big, too small, cover crop maths, the generation row, page selection,
    downstream reset;
  - the batch and single background endpoints ignore `style`;
  - the pose endpoint ignores `pose_mode`;
  - a legacy page still exports, and an uploaded background plus a compose replaces its PAGE image;
  - the queue is oldest first;
  - preflight lists only Qwen models.
- Unit tests for the new behaviour:
  - `materials/batch` counting and skip rules;
  - automatic selection of the first finished background or pose;
  - the `page.action_prompt` migration and API;
  - pass without a reason storing the automatic `forced_pass_reason`.
- Frontend tests:
  - no sketch UI, no mode switches;
  - the two-step editor (構圖 / 補線);
  - auto-compose debounce, and the line-edits confirmation;
  - the upload button and its errors, and the 「上傳」 badge;
  - the 故事 page: AI/upload toggle, saving, the batch button count;
  - the paste parser: both formats, heading variants, colon variants, header row, bad segments, the 40-page
    cap, over-long fields;
  - import: fill-empty-only, extra pages created, nothing deleted, the copy-prompt button;
  - 完成這一頁 with and without gaps;
  - the GPU pill states;
  - phone-width layout.
- e2e (fake backend): upload a background → compose → line.
- Real GPU acceptance: a new 3-page book with **all inputs in Chinese**, through the new flow:
  1. 角色;
  2. 故事 with 2 AI backgrounds and 1 upload, then 「產生全部素材」 once;
  3. 構圖 with auto-compose;
  4. 補線, then 完成;
  5. export PDF.
  Record times against Phase 7/8.
- Every existing book in `storage/app.db` still opens and exports. Back up the DB before the first run, as
  in Phase 7.

## 6. Calls made by the author (not decided by the user)
- The experiment pass bars in §3 (2/4 usable, consistency ≥ 3.5, bunny regression ≥ 4.0, within 20%,
  5/6 content, measured against English where English itself misses).
- Uploads use a centred cover crop. There is no crop or position editor in this phase.
- An uploaded background is stored as a BACKGROUND generation with `engine: "upload"`, so the existing
  history and selection logic works unchanged.
- Sketch and ControlNet columns stay in the DB, unused.
- Queue grouping is removed, not just left idle.
- Auto-compose asks once before dropping existing line fixes (§4.5).
- The action text gets its own page column, `page.action_prompt`.
- Finished batch results are selected automatically, but only into empty slots.
- The GPU pill's local states; the cloud wording waits for the cloud phase.
- Page reordering stays in the editor, not in 故事.
- Details of the paste import: the 40-page cap, the accepted heading and label variants, the spreadsheet
  format, and the 「複製給 AI 的指令」 helper.
- Hint wording above can be adjusted in review.
