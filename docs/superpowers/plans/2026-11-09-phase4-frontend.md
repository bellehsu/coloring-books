# Phase 4 前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做出內部使用的網頁，讓一個人能在瀏覽器裡完成一本繪本：建書 → 產生並設定角色 → 每頁畫構圖、挑圖、調線條、修線、QA、通過 → 匯出 PDF。

**Architecture:** `frontend/` 是 Next.js（App Router、TypeScript）。瀏覽器只呼叫同源 `/api/*`，由 Next rewrites 轉給 FastAPI（`BACKEND_URL`，預設 `http://127.0.0.1:8000`）。資料用 TanStack Query 抓取與輪詢；API 型別由後端 OpenAPI 產生。構圖畫板是點陣畫布（可擦除上傳的參考圖）；修線是向量筆畫（相對座標，對應後端修改圖層）。

**Tech Stack:** Next.js 16.4、React 19.3、TypeScript、TanStack Query 5.107、CSS Modules + CSS 變數、Vitest 5.1 + Testing Library、Playwright 1.65、openapi-typescript 7.13；Node 24。

**Spec:** `docs/superpowers/specs/2026-11-06-coloring-book-web-design.md`（§6 使用流程、§7 QA、§8 修線、§2 部署）。畫面以使用者 2026-11-09 確認的草圖為準：https://claude.ai/artifact/ESQLLeoAaMA4rh2TFEn717（三個畫面；QA 與匯出併入頁面編輯器；全部 RWD）。

## Global Constraints

- 介面文字一律繁體中文；`<html lang="zh-Hant">`；字型 Noto Sans TC（`next/font/google`）。
- 設計 token（草圖）：底色 `#F4F3EF`、表面 `#FFFFFF`、舞台 `#E9E7E1`（點陣 `#D2CEC5`，18 px）、文字 `#1C1B19`、次要文字 `#5E5B55`／`#4A4741`、邊框 `#E2DFD8`／`#D9D5CC`、主色 `#3B4FD8`（hover `#2A3AA8`、淡底 `#E6E9FB`／`#F3F5FE`）、成功 `#1E7A46`（淡底 `#E3F2E8`、字 `#1E6B3F`）、警告 `#A34F06`（淡底 `#FDECDC`、字 `#8A3F05`）、錯誤 `#B42318`（缺口紅圈 `#D92D20`）、裁切線 `#5B6EE8`、安全區 `#2E9A5C`。圓角 8／10／12／14 px。全部放在 `globals.css` 的 `:root` 變數，元件只用變數。
- 可點擊目標 ≥ 44 px（工具列按鈕 40 px 例外）；真正的 `<button>`／`<a>`／`<input>`＋`<label>`；圖示按鈕要 `aria-label`；`:focus-visible` 外框 `3px solid #8EA0F5`。文字對比 ≥ 4.5:1。不用 emoji 當圖示。
- RWD：每個畫面在 390 px 寬不可出現水平捲動。頁面編輯器斷點：> 1180 px 三欄（236 px／彈性／332 px）；761–1180 px 兩欄、檢查面板移到下方；≤ 760 px 單欄、頁面清單改成橫向捲動。角色工作室 ≤ 980 px 單欄。
- 以滑鼠操作為主（使用者決定）；不做筆壓與觸控手勢，但 pointer events 要讓觸控也能畫。
- 前端只用後端的邏輯 API，不接觸 ComfyUI（CLAUDE.md）。後端位址只來自環境變數 `BACKEND_URL`。
- 生成進度用輪詢：有 `queued`／`running` 的 generation 時每 2 秒；沒有就停。佇列狀態每 5 秒。
- 修改圖層座標規則與後端相同：`x = px / (w - 1)`、`y = py / (h - 1)`（`lineart_render.apply_strokes`）；補線寬＝頁面 `line_width_px`，擦除寬預設 24 px（300 DPI px）。
- 同一頁的修改圖層寫入必須依序送出（後端以最後寫入為準，見 Phase 3 final review Minor #8）。
- 角色檢查一律帶目前 `selected_generation_id`（Phase 3 final fix #5）。
- 測試：前端 `cd frontend && npm test`（Vitest）、`npm run e2e`（Playwright，見 Task 12）；後端維持 `env -u PYTHONPATH /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`。
- 不新增後端依賴、不下載模型、不改 ComfyUI workflow。

## Review Focus

1. 連續快速畫好幾筆修線（或畫線時網路慢）→ 寫入依序送出，最後伺服器上的筆畫等於畫面上的筆畫，不會掉筆。（Task 8 `writeQueue.test.ts`）
2. ComfyUI 沒開時按「產生」→ 候選維持「排隊中」、上方狀態顯示「生成引擎無法連線」，恢復後自動更新；沒有進行中的任務時停止輪詢。（Task 3 `hooks.test.ts`、Task 4 `QueuePill.test.tsx`）
3. 後端回 409／422（例如「請先設定角色」「線條有缺口」）→ 畫面顯示後端給的中文訊息，不是空白或「Error」。（Task 3 `client.test.ts`、Task 12 e2e）
4. 畫板全部清空後存檔 → 不上傳空白草圖，而是刪除既有草圖（後端就不使用構圖）。（Task 7 `SketchCanvas.test.tsx`）
5. 手機寬 390 px 開三個畫面 → 沒有水平捲動，編輯器變單欄、頁面清單橫向捲動。（Task 12 `rwd.spec.ts`）

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/app/api/books.py`、`pages.py`、`schemas.py`（改） | 書的統計與封面、刪除頁面、QAOut 加 near_miss_count |
| `scripts/export_openapi.py`（新） | 輸出後端 OpenAPI JSON 給前端產生型別 |
| `scripts/dev_fake_backend.py`（新） | 不需 GPU 的假生成後端，給前端開發與 e2e |
| `frontend/`（新） | Next.js 專案：設定、`src/app` 路由、`src/api`、`src/lib`、`src/components`、`src/features`、`e2e/` |
| `docs/deploy-intranet.md`、`deploy/`（新） | 內網部署步驟與服務設定範例 |

前端目錄：

```
frontend/src/
  app/layout.tsx  app/globals.css  app/providers.tsx
  app/page.tsx                                  我的繪本
  app/books/[bookId]/character/page.tsx         角色工作室
  app/books/[bookId]/editor/page.tsx            頁面編輯器（?page=<id>）
  api/openapi.json  api/schema.d.ts（產生）  api/client.ts  api/hooks.ts
  lib/layout.ts  lib/nextStep.ts  lib/checklist.ts  lib/strokes.ts  lib/writeQueue.ts  lib/raster.ts
  components/ui/*          Button IconButton Segmented Chip Dialog Drawer Toast Icons
  components/shell/*       AppBar QueuePill BookBar
  features/books/*         BookCard CreateBookDialog BookSettingsDialog
  features/character/*     CharacterStudio
  features/editor/*        EditorPage Filmstrip StageHeader SketchCanvas LineFixCanvas QACompare
                           CandidateTray Inspector ChecklistCard GeneratePanel LinePanel QAPanel ExportDrawer
e2e/  flow.spec.ts  rwd.spec.ts
```

---

### Task 1: 後端補齊前端需要的 API

**Files:** Modify `backend/app/api/schemas.py`、`backend/app/api/books.py`、`backend/app/api/pages.py`、`backend/app/api/lineart.py`；Create `scripts/export_openapi.py`；Test `tests/test_api_books.py`、`tests/test_api_pages.py`、`tests/test_api_lineart.py`、`tests/test_scripts.py`

**Interfaces（Produces）:**
- `BookOut.stats: BookStats`＝`{pages: int, passed: int, needs_fix: int, no_selection: int}`（`needs_fix`＝`qa_json` 有錯誤的頁數；`passed`＝status passed）；`BookOut.cover_url: str | None`＝position 最小、有選圖的頁的 `lineart_url`（沒有則 None）。`GET /api/books` 與 `GET /api/books/{id}` 都帶。
- `DELETE /api/pages/{id}` → 204。有 `running` 的 generation → 409「這一頁還有圖片正在生成，請稍後再刪」。先把該頁 `queued` 的 generation 設為 `cancelled`，再刪除該頁的 `LineartEdit`、`Generation`、`Page` 列與 `books/{b}/pages/{p}/` 資料夾和這些 generation 的圖片檔；其餘頁的 `position` 重新排成 0..n-1。
- `QAOut.near_miss_count: int`。
- `scripts/export_openapi.py [out_path]`：`create_app(...)` 的 `app.openapi()` 寫成 JSON（不啟動 lifespan、不需設定檔存在以外的東西；預設輸出 `frontend/src/api/openapi.json`）。

- [ ] **Step 1: Write the failing tests**：`test_book_stats_and_cover`（兩頁、一頁通過、一頁有缺口、一頁沒選圖的組合）、`test_delete_page_cascades_and_reorders`（刪中間頁：位置重排、generation 與檔案消失、queued 的被取消而非執行）、`test_delete_page_with_running_generation_409`、`test_qa_out_has_near_miss_count`、`test_export_openapi_writes_paths`（輸出 JSON 含 `/api/pages/{page_id}/lineart/edits`）。
- [ ] **Step 2:** 實作；**Step 3:** 全部後端測試 PASS；commit `feat(api): book stats/cover, page delete, near-miss count, openapi export`

---

### Task 2: 前端專案骨架與設計 token

**Files:** Create `frontend/package.json`、`next.config.ts`、`tsconfig.json`、`eslint.config.mjs`、`vitest.config.ts`、`vitest.setup.ts`、`playwright.config.ts`、`.gitignore`、`src/app/layout.tsx`、`src/app/globals.css`、`src/app/providers.tsx`、`src/app/page.tsx`（暫時的標題頁）、`src/api/openapi.json`、`src/api/schema.d.ts`；Modify 根目錄 `.gitignore`、`README.md`

- [ ] **Step 1:** 依 Tech Stack 的版本建立專案（`npm create next-app` 或手寫皆可；不要 Tailwind、不要 `src/pages`）。依賴：`next@16.4.5 react@19.3.1 react-dom@19.3.1 @tanstack/react-query@5.107.1`；dev：`typescript @types/react @types/node vitest@5.1.0 @vitejs/plugin-react jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom @playwright/test@1.65.0 openapi-typescript@7.13.0 eslint eslint-config-next`。`package-lock.json` 進版控。
- [ ] **Step 2:** `next.config.ts`：`rewrites` 把 `/api/:path*` 轉到 `${process.env.BACKEND_URL ?? "http://127.0.0.1:8000"}/api/:path*`；`output: "standalone"`。
- [ ] **Step 3:** `globals.css`：Global Constraints 的全部 token 為 `:root` 變數（`--bg --surface --stage --stage-dot --ink --muted --muted-2 --line --line-strong --accent --accent-hover --accent-soft --accent-softer --ok --ok-soft --ok-ink --warn --warn-soft --warn-ink --danger --gap --trim --safe --focus`），`body` 背景與字型、`:focus-visible` 規則、`.stage` 點陣背景 class。`layout.tsx` 用 `next/font/google` 的 `Noto_Sans_TC`（400/500/700）。`providers.tsx`：`QueryClientProvider`（`retry: 1`、`refetchOnWindowFocus: false`）＋ Toast provider 的位置（Task 4 補上）。
- [ ] **Step 4:** scripts：`dev`、`build`、`start`、`lint`、`test`（`vitest run`）、`e2e`（`playwright test`）、`gen:api`（`python ../scripts/export_openapi.py src/api/openapi.json && openapi-typescript src/api/openapi.json -o src/api/schema.d.ts`；Python 指令用 README 的 venv 路徑寫法）。先跑一次 `gen:api` 並 commit 產物。
- [ ] **Step 5:** `npm run build`、`npm test`（一個 smoke 測試：首頁渲染出「繪本工作室」）、`npm run lint` 全過。
- [ ] **Step 6:** README 加「前端」一節：安裝、`BACKEND_URL`、dev 啟動（後端 8000 + 前端 3000）、`gen:api` 何時要跑。commit `feat(frontend): Next.js scaffold, design tokens, API types`

---

### Task 3: API client 與資料 hooks

**Files:** Create `frontend/src/api/client.ts`、`frontend/src/api/hooks.ts`；Test `client.test.ts`、`hooks.test.ts`

**Interfaces:**
- `class ApiError extends Error { status: number; detail: unknown }`；`message`：`detail` 是字串就用它；是 `{message}` 就用 message；是 422 的陣列就組成「欄位：原因」；其他用「伺服器錯誤（狀態碼）」。
- `api.get<T>(path)`、`api.post<T>(path, body?)`、`api.patch<T>`、`api.put<T>`、`api.del(path)`、`api.upload<T>(path, file: Blob, method = "PUT" | "POST")`（multipart 欄位名 `file`）、`api.blob(path, method?, body?)`（回 `Blob`，給 reference-sketch）。型別來自 `schema.d.ts` 的 `components["schemas"][...]`，在 `client.ts` 匯出別名：`Book Page Generation QA Edits ExportSummary QueueStatus`。
- `hooks.ts`（query key 用陣列，例如 `["book", id]`）：`useBooks`、`useBook(id)`、`usePages(bookId)`、`useGenerations(bookId, {kind?, pageId?, includeDiscarded?})`（任一筆 `queued|running` 時 `refetchInterval: 2000`，否則 false）、`useQueue()`（5000）、`useQA(pageId, enabled)`、`useEdits(pageId, enabled)`、`useExportSummary(bookId, enabled)`；mutations：`useCreateBook`、`useUpdateBook`、`useCharacterCandidates`、`useSetCharacter`、`useCreatePage`、`useUpdatePage`、`useDeletePage`、`useReorderPages`、`usePageCandidates`、`useSelectGeneration`、`useUpdateGeneration`（discard）、`useCancelGeneration`、`useUploadSketch`、`useDeleteSketch`、`useAutofix`、`useCharacterCheck`、`usePass`、`useUnpass`。每個 mutation 成功後 invalidate 受影響的 key（寫在函式旁的註解：例如 select → `pages`、`qa`、`edits`、`book`）。

- [ ] **Step 1: Write the failing tests**（`vi.stubGlobal("fetch", ...)`）：`ApiError` 三種 detail 形狀的訊息；`upload` 送 FormData；`useGenerations` 有 queued 時 2 秒後再抓、全部 succeeded 後不再抓（fake timers）；`useSelectGeneration` 成功後 invalidate `pages` 與 `qa`。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): typed API client and query hooks`

---

### Task 4: 共用 UI 元件與外框

**Files:** Create `frontend/src/components/ui/{Button,IconButton,Segmented,Chip,Dialog,Drawer,Toast,Icons}.tsx`（各配 `.module.css`）、`frontend/src/components/shell/{AppBar,QueuePill,BookBar}.tsx`；Test 各元件 `*.test.tsx`

**Interfaces:**
- `Button`（`variant: "primary" | "secondary" | "ghost" | "danger-outline"`、`size: "md" | "lg"`、`icon?`）；`IconButton`（必填 `label` → `aria-label`）；`Segmented<T>`（`options: {value, label, badge?}[]`、`value`、`onChange`、`role="tablist"` 或 `"radiogroup"` 由 `kind` 決定）；`Chip`（`tone: "ok" | "warn" | "draft" | "busy"`，前面小圓點）；`Dialog`（置中、`role="dialog"`、`aria-labelledby`、Esc 與點背景關閉、開啟時焦點移到第一個可聚焦元素、關閉後焦點回到開啟它的按鈕）；`Drawer`（右側、寬 `min(440px, 100%)`，同樣的焦點規則）；`Toast`（`useToast().show(message, tone)`，`role="status"`，5 秒消失；錯誤用 `role="alert"`）；`Icons`：草圖用到的線條圖示（pen、brush、line、rect、circle、eraser、upload、undo、trash、wand、download、plus、close、chevron、clock、sliders、check、arrow-right）。
- `AppBar({crumbs?: {label, href?}[]})`：左側 logo＋「繪本工作室」（≤ 760 px 隱藏文字）、麵包屑、右側 `QueuePill`。
- `QueuePill`：`useQueue`；`state === "engine_unavailable"` → 橘點＋「生成引擎無法連線」；有排隊 → 綠點＋「生成佇列 N 張」；否則「佇列空閒」。
- `BookBar({book, current: "character" | "pages" | "export", onExport?, onSettings?})`：書名、版面說明（紙張・列印方式）、步驟膠囊（角色完成時打勾並連到角色工作室；頁面顯示 `passed / pages`；匯出呼叫 `onExport`）。

- [ ] **Step 1:** 測試：Dialog 的 Esc／焦點回復；Segmented 的鍵盤左右鍵切換與 `aria-selected`；QueuePill 三種狀態文字；BookBar 角色未設定時「頁面」步驟不可點且有說明。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): UI kit and app shell`

---

### Task 5: 我的繪本

**Files:** Create `frontend/src/lib/layout.ts`、`frontend/src/lib/nextStep.ts`、`frontend/src/features/books/{BookCard,CreateBookDialog}.tsx`；Modify `frontend/src/app/page.tsx`；Test `layout.test.ts`、`nextStep.test.ts`、`CreateBookDialog.test.tsx`

**Interfaces:**
- `layout.ts`：`computeLayout(size, mode, dpi = 300)` 與後端 `app.services.layout.compute_layout` 同規則、同結果（`contentMm`、`contentPx`、`generationSize`）；`ratioGroup(size)`；`PAPER_LABELS`（`a4: "A4"`、`a5: "A5"`、`b5: "B5"`、`letter: "Letter"`、`square_21: "正方形"`）。
- `nextStep(book) -> {text, tone}`：沒角色 →「下一步：設定角色」；沒有頁面 →「下一步：新增頁面」；`needs_fix > 0` →「下一步：N 頁需要修線」；有未通過 →「下一步：N 頁待 QA」；全部通過 →「可以匯出」(ok)。
- 畫面：草圖「我的繪本」——標題列（書數、建立按鈕）、卡片格線（`auto-fill, minmax(min(100%, 250px), 1fr)`；封面用 `cover_url`，沒有則用點陣底＋空白紙；進度條；`nextStep` 標籤）、最後一張虛線「建立新繪本」卡。卡片連到：沒角色 → 角色工作室，否則 → 編輯器。
- `CreateBookDialog`：書名（必填，去空白）、紙張卡（等比例小紙張圖示）、列印方式卡（白邊／出血示意）、即時顯示內容範圍與生成尺寸（`computeLayout`）、送出後導到角色工作室；後端錯誤顯示在對話框內。

- [ ] **Step 1: Write the failing tests**：`computeLayout` 與 Phase 3 `tests/test_layout.py` 相同的 5 組數值（content_px、generation_size）；`nextStep` 每個分支；對話框：空書名不能送出、改成 Letter＋家用時顯示「896 × 1152」、送出呼叫 `POST /api/books` 並導頁。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): book list and create-book dialog`

---

### Task 6: 角色工作室

**Files:** Create `frontend/src/features/character/CharacterStudio.tsx`（＋ css）、`frontend/src/app/books/[bookId]/character/page.tsx`；Test `CharacterStudio.test.tsx`

**行為（草圖「角色工作室」）:**
- 上方：描述 textarea（1–800 字，去空白）、提示文字（用英文、避免條紋與格子）、「產生 4 張」→ `POST /books/{id}/character/candidates {description, count: 4}`。
- 中間舞台：`useGenerations(bookId, {kind: "character"})`；卡片：成功顯示圖（`image_url`），排隊顯示「排隊第 N 位」（`queue_position`，0＝「生成中」＋ `progress` 進度條），失敗顯示錯誤訊息；選取（藍框＋勾）；捨棄／還原（`PATCH discarded`；捨棄的變淡，預設隱藏，「歷史 N」切換顯示）；排隊中的可取消。
- 右側：選定角色、彩色／線稿預覽切換（線稿用 `GET /generations/{id}/lineart-preview`）、提醒「設定後不能更換」、「設為本書角色，開始做頁面」→ `POST /books/{id}/character` → 導到編輯器。
- 已設定角色的書：唯讀，只顯示角色圖、描述與「前往頁面編輯器」。
- `BookBar current="character"`。

- [ ] **Step 1:** 測試：產生後出現 4 個排隊卡；選第二張再設定 → 呼叫 API 帶該 id 並導頁；捨棄後預設隱藏；已設定角色時不顯示產生按鈕；後端 409 顯示中文訊息。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): character studio`

---

### Task 7: 構圖畫板（點陣）

**Files:** Create `frontend/src/lib/raster.ts`、`frontend/src/features/editor/SketchCanvas.tsx`（＋ css）；Test `raster.test.ts`、`SketchCanvas.test.tsx`

**Interfaces:**
- `raster.ts`（純函式，傳入 `CanvasRenderingContext2D` 介面，可用假 context 測）：`drawStroke(ctx, tool, points, width)`（brush／eraser 用圓頭折線；eraser 畫白色）、`drawShape(ctx, "line" | "rect" | "circle", a, b, width)`、`isBlank(imageData, threshold = 250)`（全部像素 ≥ threshold）、`toCanvasPoint(event, rect, canvasSize)`。
- `SketchCanvas({width, height, guides, initialUrl, onSave, onUploadReference})`：內部畫布解析度＝繪本 `generation_size`；顯示時等比縮放（最寬 440 px，窄螢幕填滿）；工具列（筆刷、直線、方框、圓、橡皮擦｜上傳參考圖、復原、清除）；筆寬固定 6 px（生成解析度）；復原最多 30 步（快照）；參考線疊在上方（裁切與安全區：用 `trim_box_px`、`safe_box_px` 除以 `content_px` 得到比例；家用模式沒有參考線）；畫板中央提示「角色位置留白」直到第一筆。
- 存檔：每次落筆結束 1 秒後（debounce）呼叫 `onSave(blob | null)`：空白 → `null`（呼叫端刪除草圖），否則 PNG（白底黑線）。上傳參考圖 → `POST /books/{id}/reference-sketch` 拿到 PNG → 畫進畫布（之後可擦）→ 觸發存檔。
- 初始：有 `sketch_url` 就載入成底圖。

- [ ] **Step 1:** 測試：`isBlank`；`drawShape("rect")` 呼叫假 ctx 的 `strokeRect` 參數正確；`toCanvasPoint` 在縮放 0.5 時座標正確；清除後存檔回呼收到 `null`；復原超過 30 步時最舊的被丟棄。（jsdom 沒有 canvas：元件測試用 `HTMLCanvasElement.prototype.getContext` 的 stub。）
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): raster sketch canvas`

---

### Task 8: 修線畫布（向量筆畫）

**Files:** Create `frontend/src/lib/strokes.ts`、`frontend/src/lib/writeQueue.ts`、`frontend/src/features/editor/LineFixCanvas.tsx`（＋ css）；Test `strokes.test.ts`、`writeQueue.test.ts`、`LineFixCanvas.test.tsx`

**Interfaces:**
- `strokes.ts`：`type Stroke = {op: "draw" | "erase"; width_px: number; points: [number, number][]}`；`toRelative(px, py, w, h)`＝`[px/(w-1), py/(h-1)]`（夾在 0–1）；`simplify(points, minDistRel = 0.0015)`（去掉太近的點，單筆 ≤ 2000 點）；`validate(strokes)` 對齊後端限制（≤ 500 筆、總點數 ≤ 20000）。
- `writeQueue.ts`：`createWriteQueue(send: (strokes) => Promise<QA>)` → `{push(strokes), flush()}`：同時只有一個請求；送出期間又有新狀態時，只保留「最新的一份」，前一個完成後再送（中間版本可跳過）；回傳最後一次成功的 QA；失敗 → 回報錯誤並保留畫面上的筆畫（由呼叫端決定是否重試）。
- `LineFixCanvas({lineartUrl, contentPx, gaps, strokes, onChange, lineWidthPx})`：顯示線稿 PNG＋本機筆畫預覽（用 canvas 疊層）；工具「補線」（寬 `lineWidthPx`）「擦除」（寬 24）；縮放（按鈕 −／＋、滾輪、50%–400%）與平移（空白鍵＋拖曳或中鍵拖曳）；缺口紅圈（`gaps` 相對座標，圈直徑 30 px 螢幕尺寸）；復原＝移除最後一筆。每筆結束呼叫 `onChange(newStrokes)`。
- 呼叫端（Task 9）把 `onChange` 接到 `writeQueue`，送 `PUT /pages/{id}/lineart/edits {generation_id, strokes}`；收到 QA 後更新紅圈與 `lineart_url`（`?v=` 改變才重載圖）。

- [ ] **Step 1: Write the failing tests**：`toRelative` 角落點（0,0）與（w-1,h-1）；`simplify` 上限；`writeQueue`：連推 5 次、第一個請求未完成 → 只會再送 1 次且內容是第 5 次；前一個失敗後下一次仍會送；元件：拖曳一筆後 `onChange` 收到 1 筆、座標在 0–1。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): vector line-fix canvas with serialized writes`

---

### Task 9: 頁面編輯器（外框、頁面清單、候選圖、生成與線條設定）

**Files:** Create `frontend/src/app/books/[bookId]/editor/page.tsx`、`frontend/src/features/editor/{EditorPage,Filmstrip,StageHeader,CandidateTray,Inspector,ChecklistCard,GeneratePanel,LinePanel}.tsx`（＋ css）、`frontend/src/lib/checklist.ts`；Test `checklist.test.ts`、`EditorPage.test.tsx`、`Filmstrip.test.tsx`、`CandidateTray.test.tsx`

**行為（草圖「頁面編輯器」）:**
- 版面：`BookBar current="pages"`（匯出按鈕開 Task 11 的抽屜、設定按鈕開 Task 11 的對話框）＋三區 grid（Global Constraints 的斷點）。目前頁面存在網址 `?page=<id>`；沒有就選第一頁；書沒有角色 → 顯示提示並連到角色工作室。
- `Filmstrip`：頁數、整本進度條、＋新增（`POST pages`，新頁自動選取）、每頁縮圖（有 `lineart_url` 用它，否則空白紙）、場景摘要、狀態 Chip（通過／需要修線（`needs_fix`）／生成中（該頁有 queued/running）／草稿）；每頁「⋯」選單：上移、下移（`POST pages/order`）、刪除（確認對話框 → `DELETE`）。
- `StageHeader`：模式 Segmented（構圖草圖／線稿與修線／QA 檢查；QA 有錯誤時顯示紅色數字＝`gap_count`）、縮放顯示（修線模式）。沒有選圖時「線稿」「QA」分頁停用並說明。
- 舞台：構圖模式放 `SketchCanvas`（`onSave` → `PUT sketch` 或 `DELETE sketch`）；修線模式放 `LineFixCanvas`＋自動補線按鈕（`POST autofix`，顯示「已補 N 條」）；QA 模式放 Task 10 的 `QACompare`。`aspect_mismatch` 時舞台上方顯示提示「繪本尺寸已變更，這張圖的比例不同，建議重新生成」。
- `CandidateTray`（構圖與修線模式顯示）：本頁 generations（`kind=page`），新的在前；成功的可點選（`POST select`，選中顯示「使用中」綠框）、捨棄／還原、排隊中顯示位置與進度、可取消；「歷史 N」切換顯示已捨棄的。
- `Inspector`：最上面 `ChecklistCard`（`checklist(page, qa)` → 5 項：構圖草圖（有 `sketch_url`，否則「未畫（不使用構圖）」灰色而非錯誤）、選定圖片、線條封閉（`gap_count`）、角色外型（`character_check`）、通過 QA）。構圖模式顯示 `GeneratePanel`（場景描述 0–800 字，離開欄位時 `PATCH`；構圖強度 0–0.6 步進 0.05；「產生 4 張候選」→ `POST candidates {count: 4}`；角色未設定時停用）。修線模式顯示 `LinePanel`（細緻度 Segmented、線寬 8–16 滑桿，放開時 `PATCH`；說明修線是獨立圖層）。QA 模式顯示 Task 10 的 `QAPanel`。

- [ ] **Step 1:** 測試：`checklist` 各組合；`EditorPage` 從網址選頁、切頁會更新網址；沒選圖時線稿分頁 disabled；`CandidateTray` 選圖呼叫 select 並出現「使用中」；`Filmstrip` 刪除需確認、確認後呼叫 DELETE。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): page editor shell, filmstrip, candidates, settings`

---

### Task 10: QA 模式

**Files:** Create `frontend/src/features/editor/{QACompare,QAPanel}.tsx`（＋ css）；Test `QAPanel.test.tsx`

**行為（草圖 QA 分頁）:**
- `QACompare`：三欄（`auto-fit, minmax(min(100%, 200px), 1fr)`）：本書角色圖、本頁彩圖、`/pages/{id}/qa/overlay.png?v=<key>`（含缺口紅圈與裁切／安全區）。
- `QAPanel`：
  - 自動檢查：錯誤（紅底）與提醒（橘字）列表，用 `QAOut.errors / warnings` 的中文訊息；有缺口時「自動補線」按鈕。
  - 角色外型：「一致／不一致」radiogroup ＋ 註記（≤ 500 字）→ `POST character-check {generation_id: page.selected_generation_id, result, note}`；收到 409（圖片已更換）→ 顯示訊息並重新抓頁面。
  - 「標為通過」：條件不足時按鈕灰色並說明原因（同草圖文字）；有缺口且角色一致時，旁邊有「強制通過…」連結 → 對話框輸入原因（必填、≤ 500 字）→ `POST pass {force_reason}`；`POST pass` 的 409 `detail.errors` 列在畫面上。已通過 → 「已通過・退回草稿」→ `POST unpass`。
  - 「下載本頁 PNG」連到 `/api/pages/{id}/print.png`（`download` 屬性）。

- [ ] **Step 1:** 測試：未確認角色時通過按鈕停用且顯示「請先確認角色外型」；不一致時沒有強制通過；強制通過需填原因；character-check 送出的 `generation_id` 是目前選定圖。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): QA mode`

---

### Task 11: 匯出抽屜與繪本設定

**Files:** Create `frontend/src/features/editor/ExportDrawer.tsx`、`frontend/src/features/books/BookSettingsDialog.tsx`（＋ css）；Test `ExportDrawer.test.tsx`、`BookSettingsDialog.test.tsx`

**行為:**
- `ExportDrawer`（草圖匯出抽屜）：開啟時抓 `export/summary`；顯示已通過／未通過數字、版面說明（`computeLayout` 的頁面尺寸文字：家用＝紙張大小、印刷廠＝紙張＋出血）、範圍選項（只有通過的／所有已選圖的，各自頁數）、不會匯出的頁面與原因（`no_selection` →「還沒選圖」、`not_passed` → 依頁面狀態顯示「需要修線」或「未通過 QA」）；「下載 PDF」→ `fetch` `export.pdf?include=...` 取得 Blob 後觸發下載（檔名取自 `Content-Disposition` 的 `filename*`）；409 → 顯示後端訊息。
- `BookSettingsDialog`：書名、紙張尺寸（只有同比例群組可選，其他顯示停用並說明「只能改成同比例的尺寸」）、列印方式；送出 `PATCH /books/{id}`；改了尺寸或列印方式時先提示「線稿會重新計算，有缺口的頁面會退回草稿，記得再跑一次自動補線」。

- [ ] **Step 1:** 測試：沒有通過頁時下載按鈕停用並顯示原因；include 切換改變請求網址；設定對話框中 A4 書不能選 Letter；改列印方式時出現提醒。
- [ ] **Step 2:** 實作；**Step 3:** PASS；commit `feat(frontend): export drawer and book settings`

---

### Task 12: 假生成後端與端到端測試

**Files:** Create `scripts/dev_fake_backend.py`、`frontend/e2e/flow.spec.ts`、`frontend/e2e/rwd.spec.ts`；Modify `frontend/playwright.config.ts`、`README.md`；Test `tests/test_scripts.py`（假後端可啟動）

**Interfaces:**
- `scripts/dev_fake_backend.py --port 8000 --storage <dir>`：用 `create_app(settings, engine=DemoEngine(), start_worker=True)` 啟動 uvicorn；設定從 `load_settings()` 讀，但 `paths.storage_dir`／`paths.database` 換成 `--storage`（預設暫存資料夾）。`DemoEngine` 依請求種類畫一張 832×1216（或請求的寬高）彩色圖：白底、黑色粗外框的角色（橢圓耳朵、圓頭、方身體）與幾個背景方塊，**其中一處刻意留 20 px 缺口**（讓 QA 與自動補線有東西可測）；每張延遲 0.3 秒並回報 3 次進度。
- `playwright.config.ts`：`webServer` 兩個——假後端（`--port 8010`、暫存 storage）與 `next dev -p 3010`（`BACKEND_URL=http://127.0.0.1:8010`）；使用已安裝的 chromium（`~/.cache/ms-playwright/chromium-1243`，若版本不合，用 `executablePath` 指向它或在 README 寫明 `npx playwright install chromium`）。
- `flow.spec.ts`：建 A4 印刷廠書 → 產生角色 → 等 4 張 → 設定 → 編輯器新增頁 → 在畫板畫一個方框 → 產生候選 → 選一張 → 線稿分頁看到紅圈 → 自動補線後缺口 0 → QA：一致 → 標為通過 → 匯出抽屜下載 PDF（檢查下載檔開頭是 `%PDF`）。另一個測試：沒設角色就產生頁面候選 → 看到「請先設定角色」。
- `rwd.spec.ts`：視窗 390×844 與 1440×900 開三個畫面，`document.documentElement.scrollWidth <= innerWidth`；390 時編輯器的頁面清單 `overflow-x` 為 auto。

- [ ] **Step 1:** 寫假後端與它的測試；**Step 2:** 寫 e2e；**Step 3:** `npm run e2e` 全過；README 寫 e2e 的跑法；commit `test(e2e): fake backend and browser flows`

---

### Task 13: 內網部署

**Files:** Create `docs/deploy-intranet.md`、`deploy/coloring-books-backend.service`、`deploy/coloring-books-frontend.service`、`deploy/windows-portproxy.ps1`；Modify `README.md`

- [ ] **Step 1:** `docs/deploy-intranet.md`（繁中）：
  1. 前端正式建置：`cd frontend && npm ci && npm run build`；`node .next/standalone/server.js`（`PORT=3000`、`HOSTNAME=0.0.0.0`、`BACKEND_URL=http://127.0.0.1:8000`），靜態檔複製步驟（standalone 需要 `.next/static` 與 `public`）。
  2. 後端以單一 process 服務啟動（`uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000`，不可加 `--workers`）；ComfyUI 只綁 127.0.0.1。
  3. WSL2 使用 systemd user service（`deploy/*.service`，路徑用 `%h` 或註明要改的地方）；沒有 systemd 時的 `nohup` 替代。
  4. Windows：`deploy/windows-portproxy.ps1`（系統管理員 PowerShell；取 WSL IP、`netsh interface portproxy add v4tov4 listenport=3000 connectaddress=<WSL IP> connectport=3000`、`New-NetFirewallRule` 只開 3000、WSL 重開機後 IP 會變需重跑）。只開前端 3000，後端與 ComfyUI 不對外。
  5. 驗證：同網段另一台電腦開 `http://<Windows IP>:3000`。
- [ ] **Step 2:** README 連到這份文件；commit `docs: intranet deployment`

---

### Task 14: 實機驗收（controller 親自做）

- [ ] 確認 GPU 空閒後啟動 ComfyUI（8190）、真後端（8000，暫用 storage）與前端（`next build && next start`）。用 Playwright（腳本放 scratchpad）走一遍 Task 12 的流程，但用真生成；截圖 1440 與 390 寬的三個畫面，放進 `experiments/results/phase4_ui_check_<ts>/`（截圖不進版控，NOTES.md 進版控）並寫下問題。
- [ ] 量：從建書到一頁通過的操作步數與等待時間（對照「一天 20 頁」目標）。

---

## Self-Review 紀錄

- Spec 覆蓋：§6.1 建書時選尺寸與列印方式（Task 5）、換尺寸（Task 11）、參考線（Task 7、10）；§6.2 角色工作室（Task 6）；§6.3 頁面編輯器：頁面清單新增與排序（Task 9）、畫板五種工具＋清除＋上傳參考圖（Task 7）、空白畫板不用構圖（Task 7）、參數與候選（Task 9）、即時調線條（Task 9）、修線（Task 8）；§6.4／§7 QA（Task 10）；§6.5 匯出（Task 11）；§2 內網部署（Task 13）；排隊位置與取消（Task 6、9）。
- 草圖決定：QA 與匯出在編輯器內（Task 10、11）、三個畫面、RWD（Global Constraints、Task 12）。
- 型別一致：`Stroke` 欄位名與後端 JSON 相同（`op`、`width_px`、`points`）；`checklist`、`nextStep`、`computeLayout` 名稱在各 Task 一致。
