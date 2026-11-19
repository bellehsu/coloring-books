# 編輯頁「主角位置」快捷鈕 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 編輯頁合成舞台加「位置：靠左｜置中｜靠右」「大小：小｜中｜大」兩組按鈕，一按就移動／縮放角色並自動合成。

**Architecture:** 純函式放在 `frontend/src/lib/placement.ts`（算新位置、判斷目前符合哪個預設）；`ComposeStage` 在既有按鈕列上方加兩組 `aria-pressed` 按鈕，呼叫既有的 `onChange`（EditorPage 已負責存檔＋自動合成）。後端不動。

**Tech Stack:** Next.js + React + TypeScript、vitest + Testing Library、CSS modules。

**Spec:** `docs/superpowers/specs/2026-11-19-position-presets-design.md`

## Global Constraints

- 位置 cx：靠左 0.26、置中 0.5、靠右 0.74。大小 height：小 0.42、中 0.6、大 0.78。
- 改位置只改 cx；改大小只改 height，cx 不變，底緣（cy + height/2）不變。翻轉不變。
- 結果經 `clampPlacement`；「目前符合」容許誤差 0.005。
- 每按一次只呼叫一次 `onChange(next)`；結果與目前相同時不呼叫（同「重設位置」）。
- 前端測試指令：`cd frontend && npx vitest run`（ROS 環境變數不影響前端）。

## Review Focus

- 底緣靠近上方時放大（例如 cy 0.2、height 0.2 → 大）：cy 會被夾到 0 以上，結果仍合法、不丟錯。
- 拖曳後的任意位置：兩組按鈕都不亮，按「中」仍以目前 cx 為準。
- 已翻轉的角色按位置／大小：flip 保持 true。
- 預設位置（DEFAULT_PLACEMENT）：亮「置中」與「中」。
- 圖片還在載入（ready=false）時按鈕仍可按且不當機（placement 不依賴圖片尺寸）。

---

### Task 1: 預設位置純函式

**Files:**
- Modify: `frontend/src/lib/placement.ts`
- Test: `frontend/src/lib/placement.test.ts`

**Interfaces:**
- Produces:
  - `export type PresetSide = "left" | "center" | "right"`、`export type PresetSize = "small" | "medium" | "large"`
  - `export const PRESET_CX: Record<PresetSide, number>`、`export const PRESET_HEIGHT: Record<PresetSize, number>`
  - `export function withSide(p: Placement, side: PresetSide): Placement`
  - `export function withSize(p: Placement, size: PresetSize): Placement`
  - `export function currentSide(p: Placement): PresetSide | null`、`export function currentSize(p: Placement): PresetSize | null`

- [ ] **Step 1: 寫失敗的測試**（`describe("position presets")`）
  - `withSide({cx:0.5,cy:0.66,height:0.6,flip:true}, "left")` → `{cx:0.26,cy:0.66,height:0.6,flip:true}`
  - `withSize(DEFAULT_PLACEMENT, "small")` → `{cx:0.5,cy:0.75,height:0.42,flip:false}`（底緣 0.96 不變）
  - `withSize(DEFAULT_PLACEMENT, "large")` → `{cx:0.5,cy:0.57,height:0.78,flip:false}`
  - `withSize({cx:0.3,cy:0.2,height:0.2,flip:false}, "large")` → height 0.78、cx 0.3、cy 0（夾住，不丟錯）
  - `currentSide(DEFAULT_PLACEMENT)` 為 `"center"`、`currentSize(DEFAULT_PLACEMENT)` 為 `"medium"`
  - `currentSide({...DEFAULT_PLACEMENT, cx:0.743})` 為 `"right"`；`cx:0.4` 時為 `null`；`currentSize` 在 height 0.5 時為 `null`
- [ ] **Step 2:** `cd frontend && npx vitest run src/lib/placement.test.ts` → FAIL（函式不存在）
- [ ] **Step 3:** 實作上述 Interfaces；`withSize` 的 cy = 舊底緣 − 新高度／2，結果交給 `clampPlacement`。
- [ ] **Step 4:** 同指令 → PASS
- [ ] **Step 5:** commit `feat(web): placement presets (left/center/right, small/medium/large)`

### Task 2: 編輯頁按鈕

**Files:**
- Modify: `frontend/src/features/editing/ComposeStage.tsx`、`frontend/src/features/editing/ComposeStage.module.css`
- Test: `frontend/src/features/editing/ComposeStage.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 `withSide`、`withSize`、`currentSide`、`currentSize`。

- [ ] **Step 1: 寫失敗的測試**
  - 「位置與大小按鈕：預設位置時『置中』『中』亮著」：`getByRole("group",{name:"主角位置"})` 內有按鈕「靠左」「置中」「靠右」，`置中` 的 `aria-pressed="true"`；`getByRole("group",{name:"主角大小"})` 內「小」「中」「大」，`中` 亮。
  - 「按『靠右』呼叫一次 onChange，只改 cx」：placement 為 `{...DEFAULT_PLACEMENT, flip:true}`，按「靠右」→ `onChange` 被呼叫一次，參數 `{cx:0.74,cy:0.66,height:0.6,flip:true}`。
  - 「按『小』：腳留在原處」：placement 為 DEFAULT_PLACEMENT，按「小」→ `{cx:0.5,cy:0.75,height:0.42,flip:false}`。
  - 「已經是該選項時再按不呼叫 onChange」：預設位置按「置中」→ 未呼叫。
  - 「拖曳過的位置兩組都不亮」：placement `{cx:0.4,cy:0.5,height:0.5,flip:false}` → 六顆都 `aria-pressed="false"`。
- [ ] **Step 2:** `cd frontend && npx vitest run src/features/editing/ComposeStage.test.tsx` → 新測試 FAIL
- [ ] **Step 3:** 在 `.bar`（水平翻轉那排）上方加一列 `.presets`：兩個 `role="group"`（`aria-label` 「主角位置」「主角大小」），各有可見小標「位置」「大小」和三顆 `className={styles.mini}` 的 `aria-pressed` 按鈕；結果與目前 `same()` 時不呼叫 `onChange`。CSS：`.presets` 與 `.bar` 同樣 flex-wrap、置中、gap 8px；手機寬度會自動換行。
- [ ] **Step 4:** 同指令 → PASS；再跑 `npx vitest run` 全部、`npx tsc --noEmit`、`npx eslint src/features/editing src/lib`
- [ ] **Step 5:** commit `feat(web): editor position and size preset buttons`

### Task 3: 用瀏覽器實際操作驗收

- [ ] 在驗收環境（後端 :8001 用 scratchpad packstore、前端複本 :3001）打開 book 8 第 5 頁（中正紀念堂）的編輯頁，按「靠右」「小」，等「✓ 線稿已自動更新」，截圖桌面與手機寬度（390px），傳給使用者。不需要 GPU。
