# Queue grouped by model + generate all backgrounds at once: design

Date: 2026-11-10. Follows on from Phase 6 (precise/fast pose modes).

## 1. Why

Backgrounds use SDXL and precise poses use Qwen-Image-Edit. The two model families together (about 33 GB) exceed the 31 GB of system RAM, so ComfyUI reloads models on every switch. In the real-GPU acceptance (`experiments/results/phase6_qwen_pose_ui_check_20261110_0257/`), backgrounds went from 6 s to 18–27 s, poses took 30 s, and a page took about 75 s. The current flow, page by page "background → pose", switches models twice per page.

The user chose (2026-11-10) "group the queue by model + generate all backgrounds at once". Two parallel experiments are running alongside this design: a smaller Qwen text encoder, and generating backgrounds with Qwen too. Neither affects this design.

## 2. Goals, non-goals, anti-goals

**Goals**
- When several jobs are waiting, run all jobs for the same model family together, to cut switching.
- One button queues a background for every page that has a scene description and doesn't have one yet. The user can write every page's scene first, generate all backgrounds in one go, and then do the poses page by page, so the whole book switches models about once.
- The 「排隊第 N 位」 shown on screen must match the actual run order.

**Non-goals**
- No batch pose generation (action text is not stored per page; out of scope this time).
- No change to the ComfyUI side or its launch flags.
- No new models.

**Anti-goals**
- A job is never left waiting forever (starvation).
- Queue position on screen must not disagree with the actual order. If the user sees 「第 1 位」 but it runs later, that counts as a failure.
- Batch generation must not change pages that already have a background.

## 3. Behaviour

### 3.1 Model family
- `qwen`: POSE with `pose_mode = precise`.
- `sdxl`: everything else (CHARACTER, PAGE, BACKGROUND, POSE fast).
- COMPOSITE does not go through the queue.
- The family is derived from the generation's kind and `request_json`; no new database field.

### 3.2 Picking the next job (worker)
- The worker remembers the family of the last job it ran (in memory; unknown after a restart).
- The next job is the oldest waiting job in the same family. If there is none, it is the oldest waiting job overall.
- **Anti-starvation:** if the oldest waiting job overall has waited longer than `queue.max_wait_s` (setting, default 180 s), it goes first.
- One pure function `plan_order(queued, last_family, now, max_wait_s) -> list[id]` defines the order. It is used both for picking the next job and for computing `queue_position`, so the two always agree.

### 3.3 Generate all backgrounds at once
- `POST /books/{book_id}/backgrounds/batch` (no body).
  - For each page in the book, in `position` order, queue one background (same parameters as the single-background endpoint, including the sketch) if all of these hold:
    - `scene_prompt` is not blank;
    - no background is selected, and there is no successful, non-discarded background;
    - there is no queued or running background job.
  - Response: `{queued: GenerationOut[], skipped: [{page_id, reason}]}`. Reasons (zh-Hant):
    - 「沒有場景描述」
    - 「已有背景」
    - 「背景產生中」
- **Editor UI:** add a button 「產生所有背景」 to the top of the page list (Filmstrip).
  - Before running it shows a confirmation with the counts: 「會為 N 頁產生背景（M 頁略過：…）」.
  - Disabled when N = 0, with a reason shown in `title`.
  - When done, refresh the history and generation queries for each page.
- The 背景 tab gains a hint line: 「先寫好每一頁的場景描述，再按頁面清單上方的『產生所有背景』，可以減少等待」.

## 4. Settings

- `config/settings.yaml` gains `queue.max_wait_s: 180`, overridable with `APP_QUEUE__MAX_WAIT_S`. The settings model needs the field added.

## 5. Success criteria

- Unit tests, with mocked ComfyUI:
  - `plan_order` puts same-family jobs first;
  - anti-starvation;
  - an empty or unknown last family falls back to oldest-first;
  - `queue_position` agrees with the actual run order;
  - batch background eligibility and the three skip reasons;
  - the frontend button, the confirmation and the disabled state.
- Real GPU: for a 3-page book, write all scenes → 產生所有背景 → precise pose page by page. Record total time, compare with the 3 pages × about 75 s in phase6, and write it to `experiments/results/`.

## 6. Calls made by the author

- Anti-starvation uses wait time (180 s) rather than a skip count: simple and easy to explain.
- Last family is not persisted. After a restart, the first job is chosen oldest-first; it only affects the first pick.
- Batch generation skips pages that already have a background: one selected, or one successfully generated and not discarded (pick it from the history; this avoids generating duplicates). It also skips pages whose background is currently generating.
- The button lives in the page list, not on the 背景 tab, because it acts on the whole book.
