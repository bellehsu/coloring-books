# Pose Expressions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Regenerated poses differ visibly; the pose card gains 表情 and 面向 groups plus 8 more actions; the pose template stops inventing objects.

**Architecture:**
- Backend: a template constant, a variation list, and a "previous variation" lookup in `_pose_job`, which both single and batch requests use.
- Frontend: preset data in `frontend/src/lib/actions.ts`, plus two single-choice chip groups in the pose card of `ComposeWorkspace.tsx`.

**Tech Stack:** FastAPI, SQLModel, Next.js 16, React 19, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-11-pose-expressions-design.md` (§3 is binding, exact phrases).

## Global Constraints
- Worktree `/ai-data/repos/coloring-books-phase12`, branch `phase12-pose`.
  - Never touch `/ai-data/repos/coloring-books` and never read `scripts/dev.env`.
  - Ports 3000, 8000 and 8188 are off-limits; Playwright uses 8010 and 3010.
- Backend tests: `env -u PYTHONPATH PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`.
- Frontend checks in frontend/: `npx vitest run`, `npm run lint`, `npx tsc --noEmit`, `PYTHON=/ai-data/venvs/ubuntu24.04/coloring-books/bin/python npm run e2e`.
  - Run `npm ci` first if node_modules is missing.
- Copy phrases verbatim from the spec. Commit locally and never push. Every commit message ends with:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`

## Review Focus
1. **Exclusion.** A regenerated pose must never reuse the page's previous variation. This applies to both single and batch requests, and to a previous generation that failed or was discarded. Task 1 tests each case.
2. **Facing + variation.** When the action text has a facing phrase, the only variations allowed are 3 and 4. Task 1 tests this case-insensitively.
3. **One phrase per group.** Picking a second expression or facing replaces the first. The text never holds two. Task 2 tests this, including text the user typed by hand that already contains a phrase.
4. **Length.** A chip that would exceed 300 characters is disabled. Task 2 tests this.
5. **390 px.** The three chip groups don't overflow horizontally. This is checked in Task 2's e2e.

---

### Task 1: Backend — T4 template, pose variations, exclusion
**Files:**
- Modify: `backend/app/services/prompts.py` (`QWEN_POSE_TEMPLATE`, `QWEN_POSE_VARIATIONS`, `pick_pose_variation(rng, exclude=None, facing=False)`, `FACING_PHRASES`, `has_facing(action) -> bool`)
- Modify: `backend/app/api/compose.py` (`_last_pose_variation(session, page_id)`, `_pose_job`)
- Modify: `tests/test_prompts.py` and the compose tests

- [ ] **Step 1: Write the failing tests.**
  - `QWEN_POSE_TEMPLATE` equals spec §3.1 exactly.
  - `QWEN_POSE_VARIATIONS` equals spec §3.2, in order.
  - `pick_pose_variation(rng, exclude=v)` never returns `v` across 50 seeds.
  - With `facing=True`, it only returns variations 3 and 4, and still excludes `v`.
  - `has_facing` is true for each of the three phrases in any case, and false for Chinese text.
  - Compose:
    - two consecutive pose requests on a page use different variations;
    - the previous one counts even if it FAILED or was discarded;
    - a batch request excludes the page's last variation;
    - an action with "facing toward the left side of the picture" only gets variations 3 or 4;
    - `request_json.variation` is stored.
  - Update the existing T3 tests to T4.
- [ ] **Step 2:** Confirm the tests fail.
- [ ] **Step 3:** Implement. Reuse `pick_variation` and the shape of `_last_simple_variation`.
- [ ] **Step 4:** Run the full backend suite.
- [ ] **Step 5:** Commit `feat: pose template T4 and regenerate variations`.

### Task 2: Frontend — more actions, 表情 and 面向 groups
**Files:**
- Modify: `frontend/src/lib/actions.ts` (`ACTIONS`, `EXPRESSIONS`, `FACINGS`, `setExclusivePhrase(text, group, phrase | null) -> string`), plus its tests
- Modify: `frontend/src/features/editor/ComposeWorkspace.tsx` (two new groups and the hint), plus its tests
- Modify: the e2e spec

- [ ] **Step 1: Write the failing tests.**
  - `ACTIONS` has the 17 entries of spec §3.3, and 開心笑 is gone.
  - `EXPRESSIONS` (8) and `FACINGS` (3) match the spec labels and phrases.
  - `setExclusivePhrase`:
    - replaces another phrase from the same group (case-insensitive, whole phrase);
    - removes the phrase when given null;
    - leaves other text and the comma separation intact.
  - Pose card:
    - the groups 「表情」 and 「面向」 exist;
    - clicking 開心 and then 大笑 leaves only the 大笑 phrase in the text;
    - clicking the active chip removes its phrase;
    - a chip that would exceed 300 characters is disabled;
    - the facing hint text shows.
  - e2e: in the editor, add 揮手, 大笑 and 朝左, then click 難過 → the action text contains 'with a sad expression', not 'laughing', and does contain 'facing toward the left'. 0 px horizontal overflow at 390 px.
- [ ] **Step 2:** Confirm the tests fail.
- [ ] **Step 3:** Implement. Reuse the existing preset chip styling and the `appendPhrase`/`hasPhrase` helpers.
- [ ] **Step 4:** Run vitest, lint, tsc, e2e and the backend suite.
- [ ] **Step 5:** Commit `feat: 表情 and 面向 chips, more action presets`.

### Task 3: Real-GPU acceptance (the controller runs this; ask the user first)
- [ ] Run spec §4 on ComfyUI 8188 directly through `qwen_edit_pose_cutout`, using the production prompt functions (`qwen_pose_prompt`, `pick_pose_variation` with exclusion).
- [ ] Contact sheets, `NOTES.md`, `POST /free`, then commit.
