# 肖像模式（放進框裡）＋華麗相框 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "放進框裡（肖像）" toggle that enlarges the character and clips it to the background frame's inner area (deterministic, no GPU), on by default for every page of the 華麗相框 pack, plus the pack's v2 shapes/actions.

**Architecture:** A pure backend module finds the frame interior (flood fill from a seed) and computes a portrait fit. `Placement` gains optional `frame` (seed) and `autofit`; the compositor clips the pasted character to the interior and, with autofit, resolves cx/cy/height first and writes them back. Two endpoints serve the stage (fit on demand, interior mask for a clipped preview). The pack only changes text and placements.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / Pillow / numpy / OpenCV; Next.js / React / TypeScript / vitest / Playwright.

**Spec:** `docs/superpowers/specs/2026-11-24-portrait-frame-fit-design.md`

## Global Constraints

- Branch `frames-pack-2026-11-24`, main checkout (no worktree). Backend tests: `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`. Frontend: `cd frontend && npx vitest run`, `npx tsc --noEmit`, `npx eslint <files>`.
- Pages without `frame` must compose **pixel-identically** to today and keep today's placement JSON and `key()` (existing composites keep being reused).
- `frame`: `null` or `{x, y}` in 0–1. `autofit`: bool, default false. Height range 0.1–1.0 without frame, 0.1–**2.0** with frame.
- Default seed `(0.5, 0.55)`. Frame counts only if the region is 5%–75% of the page and touches at most one paper edge.
- Fit: character height = interior height × 0.92 ÷ 0.62; head top at interior top + 0.08 × interior height; cx = interior bbox centre x. Named constants.
- Exact copy: button 「放進框裡（肖像）」; no-frame message 「這張背景找不到框，照一般擺放」; soft attention detail 「這張背景找不到框，照一般擺放，可以再畫一張背景」; portrait hint 「肖像：主角自動放大，框外的部分裁掉。可以拖曳或拉圓點微調；再按一次『放進框裡』關掉。」
- No new GPU work, no new queue service; every ComfyUI-free test runs without a GPU.
- `experiments/results/` is gitignored — never `git add -f` it.

## Review Focus

- An existing page whose placement JSON has no `frame`/`autofit` keys: same JSON after a save round-trip and same `key()`, so its composite is reused, not recomposed (Task 2 test).
- 「再產生一張」 background on a portrait page: autofit → refit to the new frame; non-autofit → clip to the new interior with the stored position; new background without a frame → plain composite, `frame`/`autofit` kept (Task 3 tests).
- Portrait with height > 1.0 and the background has no frame: composite clamps height to 1.0 instead of erroring (Task 3 test).
- In portrait mode, 「重設位置」 turns portrait off (frame null, autofit false, default position, flip kept) (Task 5 test).
- `frame-mask` / `frame-fit` for another owner's page/generation → 404, like the other image endpoints (Task 4 test).

---

### Task 1: frame interior + portrait fit (pure backend)

**Files:**
- Create: `backend/app/services/frame_fit.py`
- Test: `tests/test_frame_fit.py`

**Interfaces:**
- Produces:
  - `DEFAULT_SEED: tuple[float, float] = (0.5, 0.55)`
  - `find_interior(background: Image.Image, seed: tuple[float, float]) -> np.ndarray | None` — bool H×W mask (True = interior, holes filled), or None when no frame.
  - `fit_portrait(interior: np.ndarray, cutout_size: tuple[int, int]) -> tuple[float, float, float]` — `(cx, cy, height)` relative to the background (height may exceed 1.0).
  - `interior_png(mask: np.ndarray) -> bytes` — L-mode PNG, 255 = interior.

- [ ] **Step 1: failing tests** (synthetic images drawn with cv2 on a 832×1216 white canvas, lines 8 px black):
  - `test_oval_frame_found`: ellipse ring centred at (416, 600), axes (300, 480) → mask covers the seed, area fraction between 0.40 and 0.60, `mask[0, 0]` is False.
  - `test_arch_open_at_bottom_counts`: two vertical pillars + top arc, open to the bottom edge → not None.
  - `test_scene_without_frame_is_none`: horizon line across the whole width at y=800 plus a sun circle → None (region touches ≥2 edges).
  - `test_tiny_region_is_none`: seed inside a 60×60 closed square → None (< 5%).
  - `test_seed_on_line_moves_to_white`: ring whose line passes exactly through the seed pixel → still finds the interior.
  - `test_holes_filled`: oval frame with a filled 80×80 black blob at the centre-left inside → the blob's pixels are True in the mask.
  - `test_fit_formula`: interior = rectangle rows 200–900, cols 100–700 on 1216×832; cutout (400, 800) → height == pytest.approx(700*0.92/0.62/1216, abs=1e-3); top edge (cy − height/2)×1216 ≈ 200 + 0.08×700; cx ≈ 400/832.
  - `test_interior_png_roundtrip`: PNG decodes to L mode with values {0, 255}.
- [ ] **Step 2:** run `… -m pytest -q tests/test_frame_fit.py` → fails (module missing).
- [ ] **Step 3:** implement. Ink = gray < 160; dilate with a square kernel of `max(3, round(5 × width / 832))` px; `cv2.connectedComponents(free, connectivity=4)`; seed search: nearest free pixel within 3 % of width (spiral or distance transform); fill holes with `cv2.floodFill` from the border on the inverted region (or `scipy`-free equivalent); edge rule and area rule as in Global Constraints. Constants named at module top.
- [ ] **Step 4:** tests pass; whole backend suite still green.
- [ ] **Step 5:** commit `feat(backend): find a frame's interior and fit a portrait into it`.

### Task 2: Placement `frame` / `autofit`

**Files:**
- Modify: `backend/app/services/compose.py` (`Placement`), `backend/app/api/schemas.py` (`PagePlacement`), `frontend/src/api/client.ts` (`Placement`), `frontend/src/lib/placement.ts`, `frontend/src/api/schema.d.ts` + `openapi.json` (`npm run gen:api`)
- Test: `tests/test_compose.py`, `tests/test_api_compose.py`, `frontend/src/lib/placement.test.ts`

**Interfaces:**
- Produces (backend): `Placement(cx, cy, height, flip, frame: tuple[float, float] | None = None, autofit: bool = False)`; `to_dict()` omits `frame`/`autofit` when they are default; `key()` returns today's 4-tuple when both are default, else appends `(round(x,4), round(y,4), autofit)`; `validate()` height max = 2.0 if frame else 1.0; `from_dict` accepts `frame` as `{"x","y"}` or null, `autofit` bool (ValueError in Chinese otherwise).
- Produces (frontend): `Placement` gets `frame?: { x: number; y: number } | null; autofit?: boolean`; `clampPlacement` keeps them and uses max height 2 when `frame`; `PLACEMENT_MAX_HEIGHT_PORTRAIT = 2`.

- [ ] **Step 1: failing tests:** `test_placement_old_json_roundtrip_unchanged` (dict without the keys → `to_dict()` equal to input, `key()` 4-tuple); `test_placement_frame_roundtrip`; `test_height_above_one_only_with_frame`; `test_bad_frame_rejected` (x=1.5, `"frame": [0.5, 0.5]`, `"autofit": "yes"`); API: `PUT /pages/{id}/placement` with frame/autofit stores and returns them, height 1.6 without frame → 422; vitest: clampPlacement keeps frame/autofit, clamps height to 2 with frame and 1 without.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement; `PagePlacement` adds `frame: FramePoint | None = None` (`x`,`y` 0–1) and `autofit: bool = False`, height `le=2.0` with a model validator enforcing ≤1.0 when frame is None; `to_placement`/`from_json`/`PageOut.placement` carry them; regenerate schema.
- [ ] **Step 4:** backend + frontend suites green, tsc clean.
- [ ] **Step 5:** commit `feat: placements can carry a portrait frame seed and autofit`.

### Task 3: clip, autofit write-back, soft attention

**Files:**
- Modify: `backend/app/services/compose.py` (`compose`), `backend/app/services/page_compose.py` (`compose_page`, `composite_params`), `backend/app/services/pipeline.py` (step 2/5), `backend/app/services/page_state.py`
- Test: `tests/test_compose.py`, `tests/test_page_compose.py` (create if absent, else the file that covers compose_page), `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1 `find_interior`, `fit_portrait`; Task 2 `Placement`.
- Produces: `compose(background, pose, mask, placement, interior: np.ndarray | None = None) -> Image.Image` (paste mask × interior when given); composite params gain `"frame_found": bool` only when `placement.frame` is set; `ATTENTION_REVIEW_FRAME = "review_frame"`, `REVIEW_FRAME_DETAIL` (exact copy above) in `page_state.py`; `SOFT_ATTENTIONS = (ATTENTION_REVIEW_POSE, ATTENTION_REVIEW_FRAME)` and `hard_attention` uses it.

- [ ] **Step 1: failing tests:**
  - `test_compose_without_interior_is_byte_identical` (same output as before for a fixed fixture; compare to `compose(..., interior=None)` and to a stored expectation computed before the change, e.g. a hash committed in the test).
  - `test_compose_clips_to_interior` (no character pixels outside the interior; frame line pixels unchanged).
  - `test_compose_page_autofit_writes_back` (page placement after compose has fitted cx/cy/height, `autofit` still true, `frame` kept).
  - `test_compose_page_no_frame_keeps_settings_and_clamps` (background without frame, placement height 1.6 + frame → composite made, params `frame_found: false`, page placement still has frame/autofit).
  - `test_compose_page_new_background_refits` (two backgrounds with differently sized frames, autofit → different heights).
  - `test_compose_page_non_autofit_clips_with_stored_position`.
  - `test_pipeline_flags_review_frame` (one-click page task: no frame → attention `review_frame` with the exact detail, status PASSED; next run with a frame → attention cleared); `test_review_frame_is_soft` (`hard_attention("review_frame")` is False).
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement. In `compose_page`, when `placement.frame` is set: load the background before `find_composite`, `find_interior`; with autofit and an interior, replace cx/cy/height with `fit_portrait` (cutout size from `cutout(pose, mask).size`) and write `placement_json` back in the same commit as the composite selection (plain column update; no history snapshot, no `pending_user_strokes` change — the user's intent didn't change); without an interior, clamp height to 1.0 for this composite only. `find_composite` uses the resolved placement. Pipeline step 5: read the selected composite's params; `frame_found is False` → set `review_frame` (unless a hard attention), found → clear `review_frame`; keep the existing review_pose handling.
- [ ] **Step 4:** backend suite green (including `test_pipeline_byte_identity.py`).
- [ ] **Step 5:** commit `feat(backend): portrait composites clip the character to the frame interior`.

### Task 4: endpoints for the stage

**Files:**
- Modify: `backend/app/api/compose.py` (frame-fit), `backend/app/api/generations.py` (frame-mask), schema regen
- Test: `tests/test_api_compose.py`, `tests/test_api_generations.py`

**Interfaces:**
- Produces: `POST /api/pages/{page_id}/frame-fit` → `PagePlacement` JSON `{cx, cy, height, flip (page's current), frame: {x: 0.5, y: 0.55}, autofit: true}`, nothing saved; 422 `detail` = 「這張背景找不到框，照一般擺放」; existing errors when background/pose missing. `GET /api/generations/{gen_id}/frame-mask?x=&y=` → `image/png` interior mask (background kind only), 404 when no frame or not a background, owner-scoped like `/cutout`; `Cache-Control: private, max-age=3600`.

- [ ] **Step 1: failing tests:** frame-fit success values (fixture background with a frame), 422 copy, other owner 404, page without pose → existing error; frame-mask PNG size equals background, 404 no frame, 404 on a pose generation, other owner 404, x/y outside 0–1 → 422.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement (reuse `owned_page`, `owned_generation`, storage get + `find_interior`); regenerate `schema.d.ts`; add `api.frameFit(pageId)` and `frameMaskUrl(genId, frame)` to the frontend client.
- [ ] **Step 4:** suites green.
- [ ] **Step 5:** commit `feat(api): frame-fit and frame-mask for the compose stage`.

### Task 5: stage toggle, clipped preview, soft attention display

**Files:**
- Modify: `frontend/src/features/editing/ComposeStage.tsx` (+ `.module.css`), `frontend/src/features/editor/ComposeStageArea.tsx`, `frontend/src/features/fix/FixPreview.tsx`, `frontend/src/features/fix/FixPage.tsx`, `frontend/src/features/fix/FixHeader.tsx`
- Test: `frontend/src/features/editing/ComposeStage.test.tsx`, `FixPage`/`FixHeader` tests, one Playwright spec `frontend/e2e/portrait.spec.ts` against the fake backend if the e2e harness supports the new endpoints (else extend the fake)

**Interfaces:**
- Consumes: Task 4 client functions; Task 2 `Placement`.
- Produces: `ComposeStageProps` gains `onFrameFit?: () => Promise<Placement | null>` (null = no frame) and `frameMaskUrl?: string | null`; parents build them from the page id and the selected background generation id.

- [ ] **Step 1: failing tests:** button renders with `aria-pressed=false`; click → `onFrameFit` called, result passed to `onChange`; null → status shows 「這張背景找不到框，照一般擺放」 and button stays unpressed; in portrait: 位置/大小 preset buttons disabled, drag/keys still call `onChange` with `autofit: false`, hint shows the portrait copy, cutout layer has the mask style from `frameMaskUrl`; second click → `onChange({...DEFAULT_PLACEMENT, flip})` without frame/autofit; 「重設位置」 in portrait does the same; mask 404 (`frameMaskUrl` image error) → no mask and status shows the no-frame copy; FixPage/FixHeader treat `review_frame` like `review_pose` (detail shown, header "請看一下" while ready).
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement. Mask: CSS `mask-image`/`-webkit-mask-image` sized to the stage box (100 % × 100 %) on a layer that holds only the cutout image; handle and focus outline stay outside that layer. Height clamp uses the portrait max while `frame` is set.
- [ ] **Step 4:** vitest, tsc, eslint, e2e green.
- [ ] **Step 5:** commit `feat(web): 放進框裡（肖像） on the compose stage`.

### Task 6: 華麗相框 v2

**Files:**
- Modify: `frontend/src/lib/themePacks.ts`, `frontend/src/lib/themePacks.test.ts`

- [ ] **Step 1: failing tests:** captions 4/5/9 = 四葉草 Four-Leaf Clover, 六角形 Hexagon, 圓角方形 Rounded Square; scene 4 contains 「空心四葉草形」 and 「四葉草裡面是乾淨的白色」; scene 5 contains 「最上面和最下面是尖角，左右兩邊是直的，總共六條邊」; scene 9 contains 「直立方形」「四個角是圓角」; action 11 = 「頭上戴著一頂小皇冠，雙手合在胸前微笑」, action 12 = 「一手比 V，開心地笑」; every placement equals `{cx: 0.5, cy: 0.59, height: 0.42, flip: false, frame: {x: 0.5, y: 0.55}, autofit: true}`; description = 「12 種形狀的古典花紋相框，主角是框裡的半身肖像，每頁有中英文字」; remove the old circle-extra/star/cloud assertions that no longer apply.
- [ ] **Step 2:** run → fail. **Step 3:** edit FRAMES. **Step 4:** green. **Step 5:** commit `feat(web): 華麗相框 v2 — clover, hexagon, rounded square; portrait placements`.

### Task 7: GPU acceptance and browser walkthrough

- [ ] **Step 1:** GPU idle check; message roboskin-38 at start/end (≈10–15 min).
- [ ] **Step 2:** acceptance backend :8001 + frontend :3001 with the scratch packstore (as in the frames run); one-click book with the snow girl and 華麗相框; contact sheet (print PNGs) into `experiments/results/frames_pack_acceptance_20261124/v2_*`; note pages flagged review_frame.
- [ ] **Step 3:** Playwright walkthrough with screenshots: editor ① 構圖 on a pack page (portrait on, drag a bit, toggle off, toggle on); a normal scene page (another book) pressing the button → no-frame message; one-click 彩色原圖 tab shows the clipped preview.
- [ ] **Step 4:** tune constants/wording if needed (tests first), commit `fix: … after GPU review`.
- [ ] **Step 5:** send screenshots + contact sheet to the user; stop for review (merge only after the user's OK).
