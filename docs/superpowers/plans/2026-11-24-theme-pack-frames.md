# 華麗相框 Theme Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 12-page theme pack whose backgrounds are ornate classical frames of different shapes, with the character placed inside each frame.

**Architecture:** One more `ThemePack` entry in `frontend/src/lib/themePacks.ts` (same text format as the other packs, parsed by `parseStory`), with `lineDetail: "standard"` and per-page `placements`. Then a real GPU book to check the frames and tune the placement.

**Tech Stack:** TypeScript, vitest (frontend only; no backend change).

**Spec:** `docs/superpowers/specs/2026-11-24-theme-pack-frames-design.md`

## Global Constraints

- Pack id `frames`, title `華麗相框`, appended after `sports` in `THEME_PACKS`.
- 12 pages, in the spec's order; captions zh/en exactly: 橢圓形 Oval, 長方形 Rectangle, 圓形 Circle, 愛心 Heart, 星星 Star, 花環 Wreath, 拱門 Arch, 盾牌 Shield, 雲朵 Cloud, 蝴蝶結 Bow, 皇冠 Crown, 菱形 Diamond.
- Every scene: 白色背景、畫面正中間一個大大的〈形狀〉古典捲草花紋外框，框裡面和框外面都是空白；ends with the shared suffix `FRAME_STYLE + NO_PEOPLE` where `FRAME_STYLE = "，只有黑色線條、沒有塗黑的地方"`.
- No metaphors (「像…」) in scenes or actions.
- `lineDetail: "standard"`.
- `placements`: 12 × `{ cx: 0.5, cy: 0.59, height: 0.42, flip: false }` (feet at 0.80) as the starting value; Task 2 may change it.

## Review Focus

- A scene that says 皇冠 or 蝴蝶結 must not also put that object in the action (objects named in both get drawn twice) — except 皇冠's action wears a *small* crown on the head, which is intended; test pins that 蝴蝶結/禮物盒 only appear in their own field.
- Scenes mustn't mention a person; NO_PEOPLE suffix test.
- Existing test "every pack makes pages with simple line detail" must become "every pack except frames"; "其他主題包不帶位置" must exclude frames.

---

### Task 1: FRAMES pack

**Files:**
- Modify: `frontend/src/lib/themePacks.ts` (new `FRAME_STYLE`, `FRAMES` list, `THEME_PACKS` entry)
- Test: `frontend/src/lib/themePacks.test.ts`

**Interfaces:**
- Produces: `THEME_PACKS` entry `{ id: "frames", title: "華麗相框", description: "12 種形狀的古典花紋相框，主角站在框裡，每頁有中英文字", text: packText(FRAMES, FRAME_STYLE + NO_PEOPLE), lineDetail: "standard", placements }`.

- [ ] **Step 1: Write the failing tests** in a new `describe("華麗相框（使用者 2026-11-24）")`:
  - ids list in "has the vehicles pack" ends with `"frames"`.
  - parses to 12 pages, no errors; captions equal the Global Constraints list; within limits.
  - every scene contains `古典捲草花紋外框`, `框裡面和框外面都是空白`, and ends with `，只有黑色線條、沒有塗黑的地方，畫面裡沒有人`.
  - each scene contains its shape word (橢圓, 長方形, 圓形, 愛心, 星星, 花環, 拱門, 盾牌, 雲朵, 蝴蝶結, 皇冠, 菱形); no scene or action contains `像`.
  - `lineDetail === "standard"`; `placements` length 12, each `{ cx: 0.5, cy: 0.59, height: 0.42, flip: false }`.
  - Update existing: simple-line-detail test skips `frames`; "其他主題包不帶位置" filters out `zoo` and `frames`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/lib/themePacks.test.ts` — expect FAIL (no frames pack).
- [ ] **Step 3: Add `FRAMES`** (actions from the spec list) and the pack entry.
- [ ] **Step 4: Run** the same command, then `npx vitest run` and `npx tsc --noEmit` — all pass.
- [ ] **Step 5: Commit** `feat(web): 華麗相框 theme pack (12 frame shapes, character inside)`.

### Task 2: GPU acceptance and tuning

- [ ] **Step 1:** Check RoboSkin has no GPU jobs; message roboskin-7a at start (~15 min).
- [ ] **Step 2:** Real backend on acceptance port with the scratch packstore DB; one-click book with the original snow girl and the 華麗相框 pack; wait for 12/12.
- [ ] **Step 3:** Contact sheet (color + line art) into `experiments/results/frames_pack_acceptance_20261124/` (not in git); measure each frame's empty interior vs. the character box; note black fills / overlap / tiny star & diamond interiors.
- [ ] **Step 4:** Message roboskin-7a at end. If placement or wording needs changes, edit the pack + tests, re-run the affected pages, commit `fix(web): 華麗相框 after GPU review`.
- [ ] **Step 5:** Send the contact sheet to the user and stop for review (merge only after the user OKs).
