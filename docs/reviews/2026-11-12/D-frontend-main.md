# Area D：前端主流程與共用程式碼（審查報告）

範圍：frontend/src/app/**、features/{book,books,fix,setup,shared,story,admin}、components/**、api/client.ts、api/hooks.ts、lib/**、next.config.ts。
做過的檢查：逐檔讀完上述非測試檔；`npx tsc --noEmit` 通過；用專案 venv 重新匯出 OpenAPI，與 `src/api/openapi.json` 比對**無差異**（schema 沒過期）；
有比對的後端端點：PATCH /pages/{id}、PUT /placement?oneclick、export/summary、/qa、history.push_snapshot。已對照 .superpowers/sdd/*/progress.md 的 parked 項目（usePageText `edited` 不重設、返回鍵遺失 blur 存檔、Toast live region 等），以下不重報。
XSS／open redirect／前端金鑰：沒有 dangerouslySetInnerHTML／innerHTML／eval；href 都由數字 id 組成；401 只轉到固定的 /invalid-link；沒有 NEXT_PUBLIC 或金鑰；管理頁的權限由後端判斷（非管理者回 404），前端的 `is_admin` 只用來決定要不要顯示連結。**安全類沒有發現問題。**

---

### D1 [高] [bug] /books/new 用 history.replaceState 換網址：之後按「上一頁」回到這一筆，看到的是空白的「做一本新的著色書」
- 位置: frontend/src/features/setup/SetupPage.tsx:84（`window.history.replaceState(null, "", \`/books/${made.id}/setup\`)`）；Next 16.4.5 node_modules/next/dist/client/components/app-router.js:302-311、router-reducer/reducers/restore-reducer.js
- 問題: Next 會改寫 `history.replaceState`，把**目前的 router tree**（也就是 `books/new` 的 tree）抄進新的歷史紀錄（`copyNextJsInternalHistoryState`）。之後在 popstate 時，`restoreReducer` 用的是歷史紀錄裡的 tree，不是網址。所以這筆網址是 `/books/5/setup` 的紀錄，回來時會畫出 `books/new` 這個 segment，也就是 `<SetupPage bookId={null}>`。
- 會怎樣出錯: 在 /books/new 寫書名（書就建立了，網址變成 /books/5/setup）→ 選主角、貼故事 → 按「開始做這本書」，`router.push` 進到 B → 按瀏覽器「上一頁」想改故事 → 網址是 /books/5/setup，畫面卻是空白的新書表單，標題「做一本新的著色書」。這時再寫書名，會**再建一本重複的書**。重新整理就恢復正常。按「← 我的書櫃」再按上一頁，也會遇到一樣的情況。
- 建議修法: 讓 /books/new 掛載時讀 `window.location.pathname`：如果是 `/books/{id}/setup`，就用這個 id 當初始的 bookId。或者建立書之後改用 `router.replace`，並把還沒存的草稿（描述、故事、選好的上傳圖）先暫存，換頁後再放回去。
- 把握: 可能（已讀 Next 16.4.5 原始碼，確認 replaceState 會抄舊 tree、restore 用歷史紀錄的 tree；但沒在瀏覽器實測 bfcache 是否真的拿快取的 books/new segment 來畫）

### D2 [中] [bug] C 的角色位置每改一次就送一次 PUT ?oneclick：方向鍵連按會產生大量復原快照和重新合成，還會把頁面的 50 筆歷史擠光
- 位置: frontend/src/features/fix/FixPage.tsx:446-469（usePlacementSaves 每次 onChange 都串一個 save）、frontend/src/features/editor/ComposeStage.tsx:120-151（每次 keydown 都呼叫 onChange）；backend/app/api/compose.py:554-562（每次都 push_snapshot、清掉筆畫、enqueue_page_task）、backend/app/services/history.py:37（MAX_SNAPSHOTS = 50）
- 問題: ComposeStage 在拖曳時只有放開才呼叫 onChange，但方向鍵、＋／－每按一下就呼叫一次。C 的 usePlacementSaves 把每次變更都依序送成 `PUT /placement?oneclick=true`，沒有合併。後端每收到一次就存一筆快照、清掉使用者筆畫、排一次頁面流程。進階編輯走的是 placementSaver（latestByKey 會合併），C 沒有用它。
- 會怎樣出錯: 在「彩色原圖」選取角色，按住 → 鍵一秒左右（鍵盤自動重複約 30 次）→ 送出約 30 個 PUT，排 30 次重新合成，「正在重新做這一頁」閃很久；之後按「↶ 復原」要按 30 次才會回到移動前。連按超過 50 次的話，更早的步驟（例如之前換過的背景）會被擠出歷史，再也復原不了。
- 建議修法: 在 usePlacementSaves 合併：送出中又有新的變更時，只保留最新的一筆（latest-wins），等前一個請求回來再送。或者鍵盤移動加約 400 ms 的 debounce，停下來才存。
- 把握: 確定（前後端程式碼都看過）

### D3 [中] [bug] 復原／重做／還原只等位置與筆畫寫完，不等文字欄位的 blur 存檔與「中文／英文」開關的 PATCH，兩個請求會互搶順序
- 位置: frontend/src/features/fix/FixPage.tsx:186-193（runHistory 只 await `placement.idle()` 與 `flushEdits()`）、:217（`busy` 沒有包含文字或開關存檔中）、:686／:808／:982-983（onBlur → save）、:923-927（toggle）
- 問題: 在「改動作／改場景／改字」欄位輸入後直接按 ↶ 復原：mousedown 先觸發 blur，送出 `PATCH ?oneclick`（後端看到動作或字變了，會先存快照），接著 click 送出 `POST /undo`。兩個請求同時在路上，後端處理的先後順序不固定。開關的 PATCH 也一樣，CaptionRow 的 `toggling` 不在 FixBody 的 busy 裡。
- 會怎樣出錯: 改了動作文字 → 直接按「↶ 復原」→ 如果 undo 先到，被復原的是**上一步**（例如剛拖的位置），之後 PATCH 才落地，新文字留下來，重做紀錄也被清掉。如果 PATCH 先到，被復原的是文字。使用者分不出是哪一種。另外，較晚回來的 PATCH 回應會透過 putPage 把 can_undo／can_redo 和文字蓋回舊值，要等下一次 refetch 才會修正。
- 建議修法: 每列的 usePageText／toggle 把送出中的 promise 登記到 FixBody，例如一個 `pendingText` 集合或 ref。runHistory 和 accept 先 `await Promise.allSettled(...)` 等它們結束，存檔中時 busy 也設為 true。
- 把握: 可能（有競態的程式路徑確定存在；後端兩個 sync endpoint 的實際處理順序沒有實測）

### D4 [中] [bug] C 沒有 beforeunload 保護：筆畫或位置寫入中、或寫入失敗時關分頁或重新整理，不會提醒，修改就不見了
- 位置: frontend/src/features/fix/FixPage.tsx（整個檔案沒有 beforeunload）；對照 frontend/src/features/editor/EditorPage.tsx:195-199、frontend/src/features/editor/pendingWrites.ts:17
- 問題: 進階編輯在還有沒存好的位置或修線（送出中、失敗）時會請瀏覽器確認離開。C 用的是同一套修線佇列（editsQueue，oneclick key），也有自己的位置存檔串（usePlacementSaves），卻沒有掛 `onBeforeUnload`。而且 C 自己的位置串不在 `hasPendingWrites` 會看的 placementSaver 裡。
- 會怎樣出錯: 在 C 用畫筆補線，剛好遇到一次寫入失敗（例如 409「這一頁正在重新合成」或網路斷線，畫面只閃一次 toast）→ 使用者以為畫好了，直接關掉分頁 → 筆畫沒有存到伺服器，也沒有任何提醒。
- 建議修法: FixBody 加上和 EditorPage 一樣的 `beforeunload`（`onBeforeUnload(qc, e)`），再加上「usePlacementSaves 還有 inFlight」的條件。
- 把握: 確定（是否缺少這個保護已用 grep 確認：frontend/src 只有 EditorPage 有 beforeunload）

### D5 [中] [結構] FixPage.tsx 989 行，混了 13 個元件與 hook，而且依賴進階編輯的內部模組（主流程反過來靠舊編輯器）
- 位置: frontend/src/features/fix/FixPage.tsx:22-27（從 features/editor 匯入 ComposeStage、ComposeWorkspace 的 `usePageText`／`ExclusiveChips`、`Inspector.module.css` 的晶片樣式、editsQueue、errMsg、LineFixStage）；frontend/src/api/hooks.ts:9（api 層匯入 `@/features/editor/placementSaver`，層次反過來）
- 問題: C 是主流程，但它的文字存檔邏輯（usePageText）放在 539 行的 ComposeWorkspace.tsx 裡，晶片的 CSS 借用 Inspector.module.css。SetupPage、CharacterSection、BookOverview 也從 features/editor 拿 errMsg，B 的 ExportSheet 拿 ExportOptions。之後要縮減或移除進階編輯時，C 會跟著壞。FixPage.tsx 本身同時放了載入、頁首動作、預覽、四個修改列與兩個 hook，很難找。
- 會怎樣出錯: 有人只是改進階編輯的 ComposeWorkspace 或 Inspector.module.css（例如改 `.preset` 樣式、改 usePageText 的去重規則），C 的動作晶片與「圖下方的字」開關就會一起變，而且 C 的測試不一定蓋到。
- 建議修法: 拆成：`FixPage.tsx`（載入＋FixBody 版面，約 200 行）、`useFixActions.ts`（act／runHistory／flushEdits／accept／retry／acceptBlocked）、`usePlacementSaves.ts`、`FixPreview.tsx`（PrintPreview／ColorPreview／DrawStage）、`rows/{Row,PoseRow,BackgroundRow,LinesRow,CaptionRow}.tsx`（含 useSavePage）。共用的 usePageText、ExclusiveChips、errMsg、晶片樣式、ComposeStage、LineFixStage、editsQueue、ExportOptions 搬到 `src/components` 或 `src/lib`（例如 `features/shared-edit/`）。placementSaver 從 api/hooks.ts 移出，useCompose 改由呼叫端先等。
- 把握: 確定

### D6 [低] [bug] C 的線稿 QA 載入失敗時沒有錯誤訊息：按「畫筆」後一直顯示「載入線稿中…」
- 位置: frontend/src/features/fix/FixPage.tsx:134（`useQA(...).data`，忽略 error）、:531-548（DrawStage：`qa` 為 undefined 時顯示 aria-busy 的「載入線稿中…」）；backend/app/api/lineart.py:145（/qa 遇到原圖遺失或 LineartUnavailable 會回 404／409）
- 問題: FixPage 只讀 `useQA().data`，沒有讀 `isError`。QA 失敗時 DrawStage 一直轉圈，LinesRow 的副標也只顯示「線稿」，看不出是壞掉了。
- 會怎樣出錯: 選定圖在儲存空間不見了（R2 物件遺失）→ /qa 回 404「圖片檔案遺失」→ 在 C 按「畫筆」→ 永遠停在「載入線稿中…」，不會告訴使用者要去「再試一次」或換一張。
- 建議修法: 讀 `useQA` 的 error：DrawStage 和 LinesRow 用 errMsg 顯示後端訊息，再加一個重試按鈕。
- 把握: 確定

### D7 [低] [bug] C 也跟著 B 在背景分頁每 3 秒輪詢 overview
- 位置: frontend/src/api/hooks.ts:173-179（`refetchIntervalInBackground: true`）、frontend/src/features/fix/FixPage.tsx:79
- 問題: 背景分頁照樣輪詢是為了 B 的「✓ 做好了」分頁標題而設計的。C 也呼叫同一個 useBookOverview，但 C 沒有分頁標題通知，背景輪詢在 C 沒有用處。
- 會怎樣出錯: 整本書還在做時，C 停在背景分頁也會每 3 秒打一次 /overview（這個端點會查每頁的狀態），直到整本做完。
- 建議修法: 讓 useBookOverview 接收 `{ background?: boolean }` 參數，只有 B 傳 true。
- 把握: 確定

### D8 [低] [bug] B 的「印出這本書」：匯出摘要可能把頁面退回草稿，但 B 的 overview 沒有重抓
- 位置: frontend/src/features/editor/ExportDrawer.tsx:70-77（摘要更新後只 invalidate pages、book）；backend/app/api/export.py:91-96（summary 會 ensure_page_lineart，可能把 PASSED 頁退回草稿）
- 問題: ExportOptions 原本是給進階編輯用的，所以只重抓 pages 和 book。B 的頁面卡和狀態卡讀的是 overview，而 overview 在沒有工作進行時不會輪詢。
- 會怎樣出錯: 線稿 key 變了（例如舊頁的線稿參數改過）→ 在 B 打開「印出這本書」→ 摘要把某頁退回草稿，「先看一眼」不會列出它 → 關掉抽屜後，B 的卡片還顯示「可以印 ✓」，要重新進入頁面才會更新。
- 建議修法: ExportOptions 的 invalidate 一併加上 `qk.overview(book.id)`（下載 PDF 之後那一段也要加）。
- 把握: 可能（要遇到線稿 key 改變的頁，觸發條件少見）

### D9 [低] [死碼] api/hooks.ts 有 4 個 hook 已經沒有正式程式碼在用
- 位置: frontend/src/api/hooks.ts:218（useCreateBook）、:261（useCharacterCandidates）、:318（useSetCharacter）、:480（useBatchMaterials）；frontend/src/api/client.ts:15（BatchMaterials 型別只給 useBatchMaterials 用）
- 問題: Phase 13 的 A 改用 `api.post("/books")`（SetupPage.tsx:78）與 useSetupCharacter，舊的角色工作室和故事頁「產生全部素材」都已經移除。
- 會怎樣出錯: 誰都沒用。grep 了 frontend/src 與 frontend/e2e 的 `\buseCreateBook\b`、`\buseSetCharacter\b`：0 處引用。`useCharacterCandidates`、`useBatchMaterials` 只在 src/api/hooks.test.tsx 出現。因此後端 `POST /books/{id}/materials/batch`（compose.py:312）也沒有前端呼叫者（後端是否保留請 B 區確認）。
- 建議修法: 刪掉這 4 個 hook、對應的測試和 BatchMaterials 型別。
- 把握: 確定

### D10 [低] [死碼] BookBar 的「角色／故事／匯出」目前步驟分支永遠不會出現，兩顆步驟按鈕連到同一頁
- 位置: frontend/src/components/shell/BookBar.tsx:22（`current: "character" | "story" | "pages" | "export"`）、:29-30、:45-50、:72-76、:266-269
- 問題: BookBar 只剩 EditorPage.tsx:448 一個使用者，而且固定傳 `current="pages"`。`current === "character" | "story" | "export"` 的分支都走不到。「1 角色」「2 故事」兩顆都連到 /setup，這是舊的四步驟導覽留下來的。
- 會怎樣出錯: 誰都沒用：grep `<BookBar` 只有 EditorPage.tsx:448（current="pages"）與 BookBar.test.tsx。
- 建議修法: 把 BookBar 收斂成進階編輯專用的標題列，例如一顆「主角與故事」連結加上「匯出」。刪掉沒用到的 current 值。
- 把握: 確定

### D11 [低] [結構] 檔名與資料夾名稱已經對不上用途：CreateBookDialog.module.css、features/story
- 位置: frontend/src/features/books/CreateBookDialog.module.css（CreateBookDialog.tsx 已刪除，只有 BookSettingsDialog.tsx:19 還在 import）；frontend/src/features/story/{aiPrompt.ts,ThemePackDialog.tsx}（故事頁已移除，只剩 features/setup/StorySection.tsx:6-7 在用）
- 問題: 看檔名會以為還有建立書的對話框和故事頁，而且 BookSettingsDialog 同時 import 兩份 CSS（`styles` 來自 CreateBookDialog、`extra` 來自自己的）。
- 會怎樣出錯: 有人看到檔名以為是死碼就刪掉 CreateBookDialog.module.css，進階編輯的「繪本設定」對話框就會跑版。或者在 features/story 找故事頁，結果找不到。
- 建議修法: 把 CreateBookDialog.module.css 併進 BookSettingsDialog.module.css。features/story 的兩個檔案搬到 features/setup。
- 把握: 確定

### D12 [低] [結構] 還在說「故事頁」的過時註解
- 位置: frontend/src/api/hooks.ts:119-123（usePagesFollowJobs 寫「故事頁與編輯器都用」，現在是 C 與進階編輯）、frontend/src/lib/upload.ts:1（「故事頁與構圖的背景卡片共用」，現在是 A／C／進階編輯）、frontend/src/lib/actions.ts:2（「故事頁每一頁與構圖的角色動作卡片」，現在是 C 的改動作與進階編輯）、frontend/src/api/hooks.ts:257-260（useCharacterCandidates 的說明，但它已經是死碼，見 D9）
- 問題: 註解指向 Phase 13 已經移除的頁面，會讓人以為還有一個故事頁在用這些程式碼。
- 會怎樣出錯: 照註解去找故事頁的呼叫端，找不到；也會誤判修改影響的範圍。
- 建議修法: 照目前的實際使用者改寫這些註解。
- 把握: 確定

### D13 [低] [結構] 新流程裡重複的小邏輯
- 位置: `isActive` 有 4 份（hooks.ts:59、FixPage.tsx:61、CharacterSection.tsx:14、storyPreview.ts:71）；`NO_QUOTA` 有 2 份（BookOverview.tsx:34、storyPreview.ts:180）；C 的網址有 2 種寫法（BookOverview.tsx:38 的 `pageHref` 與 fixState.ts:140 的 `fixHref`）；`quiet` 有 2 份（FixPage.tsx:555、ComposeWorkspace.tsx:195）；FixBody 的 `act`＋actingRef（FixPage.tsx:149-182）與 ComposeWorkspace 的 `useSingleFlight` 是同一個模式；`useFixPages` 自己又定義了一次 `qk.pages` 的 queryFn（fixApi.ts:42-49，和 hooks.ts:99 的 usePages 相同）
- 問題: 同一條規則寫在好幾個地方，改的時候容易漏改其中一份。例如 isActive 之後如果把 `cancelling` 也算進去，FixPage 和 CharacterSection 就會和 hooks 判斷不一樣。
- 會怎樣出錯: 某一份改了、另一份沒改，造成不一致：例如 B 的 `pageHref` 改了 filter 參數名稱，C 的 `parseFilter` 讀不到。
- 建議修法: isActive 從 hooks.ts 匯出；BookOverview 改用 fixHref；NO_QUOTA 放到 lib；useFixPages 改成 `usePages` 加上 refetchInterval 選項；quiet 與 useSingleFlight 放到 lib 共用。
- 把握: 確定
