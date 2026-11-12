# Area E：進階編輯（舊頁面編輯器）code review

範圍：frontend/src/features/editor/**、frontend/src/app/books/[bookId]/editor/**、編輯器用到的 lib（strokes、placement、actions、checklist、writeQueue、latestByKey）、components/shell/BookBar.tsx（只剩編輯器在用）。
對照後端：app/api/lineart.py、compose.py、pages.py、review.py、history.py、services/pipeline.py、page_compose.py。
已排除 .superpowers/sdd/*/progress.md 已 park 的項目（舊編輯器 undo 不能移除 auto 補線、進階編輯沒有強制「對」、繼續做會 auto-PASS、M2 regen 無快照、M4 混用 redo、離開時丟掉 600 ms 內的合成、WIDTH_MIN/pxToMm 多餘 export 等）。

---

### E1 [高] [bug] C 正在等重新合成時，在進階編輯補的線會被頁面流程的重新合成丟掉
- 位置: frontend/src/features/editor/EditorPage.tsx:144, 166, 318-335, 587-598；backend/app/api/lineart.py:224-227；backend/app/services/pipeline.py:367-379
- 問題: 舊編輯器完全不看 `recompose_pending`（grep features/editor 無此欄位）。C 拖完角色後頁面 `pending_user_strokes=CLEARED` 並排了 page task，但選定圖仍是舊合成圖。這時從 C 的「進階編輯」連結（FixPage.tsx:344，一律顯示）進來，舊合成圖若有使用者筆畫，autoKey 那段不自動合成（lineEditsOf ≠ null），「下一步：補線」照常可按，使用者在**舊合成圖**上補線；非 oneclick 的 PUT 會把 pending 清成 None（「舊編輯器的修線優先」），但已排的 page task 沒被取消，流程隨後用新位置 `compose_page` 產生新合成圖並選用，舊編輯器剛補的線留在舊合成圖上、從頁面上消失。
- 會怎樣出錯: C 拖角色（流程在排隊，GPU 忙著做別頁）→ 等不及按「進階編輯」→ ② 補線畫了 10 筆、看到存好 → 幾分鐘後流程跑到這頁，選定圖換成新合成圖，10 筆全不見，也沒有「清掉補過的線？」的詢問（舊編輯器自己的合成一定會問）。這是 oneclick Task 4 M1（「舊編輯器的修線優先」）修法的後果，紀錄裡沒提到 page task 仍會重新合成。
- 建議修法: 舊編輯器在 `page.recompose_pending` 時停用補線分頁與自動合成（顯示「這頁正在重新合成…」並輪詢 pages），或後端在非 oneclick PUT/compose 清 pending 時一併取消該頁排隊中的 page task。
- 把握: 確定（程式路徑逐行對過；未實機跑）

### E2 [高] [bug] 進階編輯按「換一張」會讓流程自動換圖、重新合成，補過的線無預警消失且無法復原
- 位置: frontend/src/features/editor/ComposeWorkspace.tsx:262-272, 387-398；backend/app/services/pipeline.py:203-222, 244-258
- 問題: Phase 13 之後多數頁面是流程做的（`background_generation_id == auto_background_generation_id`，pose 同理）。舊編輯器的「換一張」不帶 oneclick，原本的語意是「多一張縮圖、讓使用者自己點選」（ComposeWorkspace 註解、Thumbs 註解），但生成成功時 `on_generation_succeeded → auto_select` 會因欄位等於 auto_* 而直接換上新圖、清掉選定的合成圖並排 page task 重新合成。舊編輯器的「清掉補過的線？」只在自己的 autoCompose 路徑問，這條路完全繞過；又因為不是 oneclick，沒有復原快照，C 的「↶ 復原」也救不回。
- 會怎樣出錯: 在進階編輯對一頁流程做好的頁補了線 → 回到 ① 構圖按角色動作「換一張」想多看一個候選 → 新圖好了自動換上、合成圖重做，補線消失、頁面狀態也被改掉；使用者得猜到要點回舊縮圖（且位置不變）才會「直接選回」舊合成圖找回補線。
- 建議修法: 舊編輯器的「換一張」改成產生後不自動換上（後端對非 take 的生成，若頁面選定圖上有使用者筆畫就不 auto_select），或前端在有補線時先問一次並改走 oneclick（有快照可復原）。
- 把握: 確定（後端 auto_select 條件與前端呼叫都對過；progress.md 只 park 了「進階編輯開始的生成也會讓頁面變可以印」，沒有提補線遺失）

### E3 [中] [bug] 舊編輯器顯示的角色位置用 session 內快取，C 改過位置後回來會顯示舊位置並在下一次微調時蓋掉 C 的位置
- 位置: frontend/src/features/editor/EditorPage.tsx:184-188, 489；frontend/src/features/editor/placementSaver.ts:226-241
- 問題: 舞台位置 = `placementSaver.latest(pageId) ?? current.placement`。`latest` 存在模組層級（整個 SPA session），只有刪頁／新頁才清；C 的位置走 `useSavePlacementC`（fixApi.ts），C 的復原／重做／還原也會改 placement，都不經 placementSaver。所以只要這個 session 在舊編輯器動過那頁，之後伺服器的位置就永遠被忽略。
- 會怎樣出錯: 進階編輯拖角色到左邊 → 到 C 把角色拖到右邊（或按復原）→ 再按「進階編輯」：舞台上角色在左邊、線稿是右邊；按一下「水平翻轉」或方向鍵 → 以左邊的舊位置存檔＋自動合成，C 的位置被覆蓋。
- 建議修法: 只在 placementSaver 忙碌或失敗時才用 `latest`（和 editsQueue 的 shouldSeedFromLocal 同規則），否則用伺服器的 `page.placement`；或 PUT 成功後清掉 `last`。
- 把握: 確定

### E4 [中] [bug] 一個修線佇列寫入失敗後，B 的「印出這本書」會一直被擋，且提示要按一個 B 上沒有的「重試」
- 位置: frontend/src/features/editor/pendingWrites.ts:290-302；frontend/src/features/editor/ExportDrawer.tsx:110-124（ExportOptions，B 的 ExportSheet 共用）；frontend/src/features/editor/editsQueue.ts:71-77
- 問題: `whenBookWritesIdle` 會 flush 每頁「目前選定圖」的佇列（舊編輯器的與 C 的 oneclick 佇列都算）；佇列最後一次失敗時 `flush()` 永遠 reject，直到同一佇列再 push。失敗的佇列刻意保留（prune 不刪），而且 registry 在模組層級，只有重新整理頁面才會清。
- 會怎樣出錯: 在進階編輯或 C 補線時網路斷一下（PUT 失敗，畫布顯示「修線尚未儲存 重試」）→ 直接回 B 按「印出這本書」→ 每次都顯示「有修改尚未儲存…請先按「重試」存好再匯出」，B 上沒有重試按鈕也沒說是哪一頁，只能猜要回到那頁的畫布或重新整理。
- 建議修法: 錯誤訊息帶出頁碼並附連結到該頁；或 ExportOptions 在失敗時提供「重送」（對失敗的佇列 push(latest)）。
- 把握: 確定（程式路徑）；觸發要先有一次寫入失敗

### E5 [中] [bug] 舊編輯器看不到 C 的頁面狀態（請看一下、製作中），「完成」與匯出範圍和 B／C 不一致
- 位置: frontend/src/features/editor/Filmstrip.tsx:222-227, 242；frontend/src/lib/checklist.ts:182-187；frontend/src/features/editor/FinishPanel.tsx:150-162；frontend/src/features/editor/ExportDrawer.tsx:55, 94；backend/app/api/review.py:63-104
- 問題: `pageStatus`／`checklist` 只看 `status`、`needs_fix`，不看 `attention`、`recompose_pending`；舊編輯器的「完成這一頁」走 `/pass`，不清 `attention`（C 的 `/accept` 會清），也不擋 `pending_user_strokes`（`/accept` 會 409）。頁面清單也不追 page task：`usePagesFollowJobs` 只在生成結束時重抓，流程在之後才把頁面標成可以印，舊編輯器不會知道。
- 會怎樣出錯: B 說第 3 頁「請看一下」→ 進階編輯顯示「完成」（綠色），在這裡按「取消完成／完成這一頁」後回 B 仍是「請看一下」；舊編輯器「匯出 PDF」預設「只有完成的頁面」會把 B 不印的「請看一下」頁印進去；C 正在重新合成的頁，舊編輯器可以把舊合成圖標成完成。流程剛把頁面做完時，頁面清單仍顯示「草稿」直到下一次操作。
- 建議修法: Filmstrip／checklist 加上 attention 與 recompose_pending 的狀態；完成改呼叫與 C 相同的語意（或後端 `/pass` 一併清 attention、擋 pending）；匯出抽屜改用 B 的 `variant="book"`（ready）。
- 把握: 確定（程式碼）；「請看一下＋已通過」要靠「換一張」失敗等情況才出現

### E6 [中] [結構] features/editor 實際上是 B／C／A 共用的程式庫，進階編輯沒辦法整個刪；editor 與 fix 互相 import
- 位置: frontend/src/features/fix/FixPage.tsx:22-27（ComposeStage、usePageText、ExclusiveChips、Inspector.module.css、peekEditsQueue、LineFixStage）；frontend/src/features/book/ExportSheet.tsx:8（ExportOptions）；setup/SetupPage.tsx:11、setup/CharacterSection.tsx:9、book/BookOverview.tsx:17、app/providers.tsx（errMsg）；api/hooks.ts:9（placementSaver）；frontend/src/features/editor/CaptionPreview.tsx:2（反向 import features/fix/fixState）
- 問題: `usePageText`／`ExclusiveChips` 放在只屬於舊編輯器的 ComposeWorkspace.tsx 裡、`ExportOptions` 放在 ExportDrawer.tsx、`api/hooks.ts` 依賴 `features/editor/placementSaver`，名稱和位置都暗示「舊編輯器專用」，其實是主流程依賴。editor ↔ fix 形成循環依賴。
- 會怎樣出錯: 誰都沒用的判斷會被誤導：有人想「進階編輯是次要路徑，整個 features/editor 刪掉」會連帶打壞 C、B 匯出、A 和 API hooks；反過來改 ComposeWorkspace 也可能意外影響 C。
- 建議修法: 把共用部分搬到 `features/shared/`（或 `components/editing/`）：ComposeStage、LineFixStage/LineFixCanvas/zoom、editsQueue/pendingWrites/placementSaver、usePageText、ExclusiveChips、ExportOptions、errMsg；chip 樣式抽出成獨立 CSS module。之後舊編輯器才是可單獨刪除的一層。
- 把握: 確定（grep `@/features/editor` 於 src、e2e）

### E7 [中] [bug] 舊編輯器與 C 的修線佇列不同 key，兩邊切換時同一頁同一張圖可能有兩個 PUT 同時在路上
- 位置: frontend/src/features/editor/editsQueue.ts:42-50, 99；frontend/src/features/editor/EditorPage.tsx:225-237；frontend/src/features/editor/FinishPanel.tsx:89-98
- 問題: oneclick 佇列與舊佇列刻意分開（key 加 `:c`），所以「同一頁依序寫入」只在同一邊成立。EditorPage 的 `flushSelectedEdits`、FinishPanel 的 `flushEdits` 只 peek 非 oneclick 佇列，不會等 C 還在送的那份；後端 PUT 是整份覆蓋、最後提交者勝。
- 會怎樣出錯: C 畫一筆（PUT 渲染線稿要一兩秒）→ 立刻按「進階編輯」→ 下一步補線、再畫一筆（舊佇列送 [a,b]）→ 若 C 的 [a] 較晚提交，伺服器只剩 [a]，畫面上仍有 b；之後沒再畫就不會補送。反方向（舊佇列失敗留著 latest，再到 C 畫，再回舊編輯器）會用舊的本機筆畫 seed，按「重試」蓋掉 C 的筆畫。
- 建議修法: 每頁＋選圖只用一個佇列、PUT 參數依呼叫端決定；或至少兩邊的 flush 都等另一邊的佇列（`peekEditsQueue` 兩個 key 都查）。
- 把握: 可能（時間窗短；未實機重現）

### E8 [低] [bug] 舊編輯器的「圖下方的文字」不知道 C 的中文／英文開關，關掉的那行照樣讓人編輯且說「會印」
- 位置: frontend/src/features/editor/ComposeWorkspace.tsx:211-230；frontend/src/features/editor/CaptionPreview.tsx:13-17
- 問題: CaptionCard 不顯示也不能改 `caption_show_zh/en`，提示寫「選填，印在列印頁的圖下方」；同時 CaptionPreview 依開關只顯示會印的行。
- 會怎樣出錯: C 把中文關掉 → 進階編輯改中文字 → 預覽上看不到剛打的字、卡片又說會印，使用者不知道為什麼。
- 建議修法: CaptionCard 在該行關閉時顯示「這行目前不印（在處理頁面可打開）」，或直接放同樣的開關。
- 把握: 確定

### E9 [低] [bug] 匯出前只等修線，不等位置存檔與排著的自動合成；註解說「確保 PDF 用畫面上的內容」不成立
- 位置: frontend/src/features/editor/pendingWrites.ts:284-302；frontend/src/features/editor/EditorPage.tsx:433-436
- 問題: `whenBookWritesIdle` 只 flush editsQueue；placementSaver 的 PUT 與 autoCompose 600 ms debounce 中的合成都不等。
- 會怎樣出錯: 拖完角色馬上按「匯出 PDF」→ 下載 → PDF 是舊位置的合成圖（頁面仍是完成），之後合成才換掉並退回草稿。
- 建議修法: 匯出前 flush placementSaver（`whenIdle` 全部頁）與 autoCompose；或修正註解說明範圍。
- 把握: 確定

### E10 [低] [bug] 進階編輯沒有回到整本書（B）或這頁 C 的連結
- 位置: frontend/src/features/editor/EditorPage.tsx:447-448；frontend/src/components/shell/BookBar.tsx:29-31
- 問題: AppBar 的書名 crumb 沒有 href；BookBar 的「角色」「故事」都連到 /setup，「頁面」就是自己，沒有 B（/books/{id}）或 C（/books/{id}/page/{pageId}）。
- 會怎樣出錯: 從 C 按「進階編輯」修完，想回去按「這頁可以印了」只能按瀏覽器上一頁，或經「角色」→ A →「回到這本書」繞兩層。
- 建議修法: 書名 crumb 連到 `/books/{id}`，並在舞台標題列加「回到處理頁面」連結（目前頁）。
- 把握: 確定

### E11 [低] [死碼] BookBar 只剩進階編輯在用，其他 `current` 分支走不到
- 位置: frontend/src/components/shell/BookBar.tsx:22, 45-57, 72-76, 114-121
- 問題: `current` 可為 "character"｜"story"｜"export"，但唯一呼叫端傳 "pages"；「角色」「故事」兩顆都連 /setup，步驟 1–4 的導覽是 Phase 13 以前的流程。
- 會怎樣出錯: 誰都沒用：grep `<BookBar` 於 src 只有 EditorPage.tsx:448；grep `current="character|story|export"` 無結果。
- 建議修法: 把 BookBar 收進 features/editor（或改寫成簡單的標題列＋匯出／設定），刪掉不會出現的分支。
- 把握: 確定

### E12 [低] [死碼] placementSaver 的 `busy()`／`busyPage()` 與 LineFixCanvas 的 ZOOM 再匯出沒人用
- 位置: frontend/src/features/editor/placementSaver.ts:183-185, 233-234；frontend/src/features/editor/LineFixCanvas.tsx:29
- 問題: `busyPage` 完全沒呼叫端；`busy()` 只在 placementSaver.test.ts 用；`export { ZOOM_MAX, ZOOM_MIN } from "./zoom"` 沒有任何檔案從 LineFixCanvas 取用（zoom.test 直接 import zoom）。
- 會怎樣出錯: 誰都沒用：grep `\.busyPage\(`、`placementSaver\.busy`、`saver\.busy` 於 src；grep `from "./LineFixCanvas"`／`@/features/editor/LineFixCanvas` 只取 LineFixCanvas、ZoomControls。
- 建議修法: 刪掉這兩個方法（測試改用 `hasPending`）與再匯出。
- 把握: 確定

### E13 [低] [死碼] 舊網址模式對照（background／pose／qa）只為書籤保留
- 位置: frontend/src/features/editor/StageHeader.tsx:15-26
- 問題: `LEGACY_MODES`、`isEditorMode` 只在 StageHeader 內部使用；程式與 e2e 都沒有產生 `mode=background|pose|qa` 的連結。
- 會怎樣出錯: 誰都沒用：grep `mode=` 於 src、e2e 只有 EditorPage 的註解與 data-mode；只剩外部書籤可能用到。
- 建議修法: 既然進階編輯已是次要路徑，可刪掉對照，未知 mode 一律回構圖（normalizeMode 已有預設）。
- 把握: 確定（是否還有使用者書籤無法驗證）

### E14 [低] [結構] 自動補線相關註解還說「補上的線會併進畫面上的筆畫」，實際是存到 auto 圖層
- 位置: frontend/src/features/editor/editsQueue.ts:32-35；frontend/src/features/editor/FinishPanel.tsx:103；frontend/src/features/editor/LineFixStage.tsx:159
- 問題: Phase 13 起自動補線寫 auto 層、不併進使用者筆畫（LineFixStage.tsx:36-37、129-130 的新註解是對的），但這三處仍寫「併進畫面上的筆畫，之後的寫入才不會蓋掉」。現在必須經過畫布的真正原因是先 flush 佇列與補線期間延後寫入（changedWhileFixing）。
- 會怎樣出錯: 讀者會以為 auto 補線混在 user 層、可被復原；以此推論 C／舊編輯器的復原行為會判斷錯。
- 建議修法: 改寫成「先讓畫面上的筆畫寫完、補線期間延後寫入；補線存 auto 層」。
- 把握: 確定

### E15 [低] [結構] latestByKey 的說明還寫「草圖 PUT／DELETE 用」
- 位置: frontend/src/lib/latestByKey.ts:2
- 問題: 構圖草圖已停用；現在唯一使用者是 placementSaver（角色位置 PUT）。
- 會怎樣出錯: 誰都沒用：grep `sketch|草圖` 於 src/lib、features/editor 只剩這行註解。
- 建議修法: 改成「角色位置 PUT 用」。
- 把握: 確定

### E16 [低] [結構] EditorPage 656 行混了網址同步、自動合成接線、補線詢問、素材選用與兩種模式的版面
- 位置: frontend/src/features/editor/EditorPage.tsx:122-159（nav／writes 同步）、239-335（autoCompose＋lossAsks＋autoKey）、503-617（compose／line 的 stage 與 panel 組裝）
- 問題: 一個元件內有 3 套模組層級狀態（placementSaver、editsQueue、autoCompose）的接線、render 中 setState 的網址同步、以及兩大段 JSX 分支；進階編輯現在多半只在修 bug 時才被打開，維護成本主要來自讀懂它（progress.md Task 13 已記「extract hook later」，至今未做）。
- 會怎樣出錯: 修 E1／E3 這類跨流程問題時需要同時理解所有狀態來源，容易漏改。
- 建議修法: 抽出 `useEditorNav`（122-159）、`useEditorAutoCompose`（239-335，含 lossAsks 與對話框）、`<ComposeStageArea>`（505-553）；EditorPage 只負責組版面。
- 把握: 確定

---

統計：高 2、中 5、低 9；bug 9、死碼 3、結構 4、安全 0（img src／href 都來自後端或數字 id，無 innerHTML；文字長度後端 PageUpdate 也有驗證）。
