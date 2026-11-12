# Area B：後端 HTTP API、儲存、身分與安全

範圍：backend/app/api/*.py、services/auth.py、storage.py、log_redact.py、services/upload_bg.py、scripts/invites.py、scripts/storage_gc.py（唯讀審查，基準 3d9e0b8；審查期間 working tree 有別人改了 schemas.py／history.py，與下列發現無關）。

先確認過、沒有問題的部分（不列成發現）：
- 擁有權：9 個 router 都掛 `require_owned_path`（main.py:102-104），每個端點另外用 `owned_*` 再載入一次；body 裡的 id（`CharacterSelect`、`SelectGeneration`、`EditsIn`、`CharacterCheckIn`、`PageOrder`、`BatchMaterialsIn`）不是走 `owned_generation`，就是只拿來當限定在本頁／本書的 UPDATE 條件，沒找到 IDOR。
- 分享：`_visible_books` 只回分享中、擁有者未撤銷、PASSED 而且有選定圖的頁；`shared_page_image` 不分擁有者先查頁面是刻意的，後面還有可見性檢查。
- HMAC：`verify` 用 `compare_digest`、先檢查過期、拒絕非 ASCII 的 sig，key 限定在 books/ 或 cache/ 底下。cookie 是 httpOnly＋Lax；token 與 session 在 DB 只存 sha256；access log 會把 token 遮掉。
- 已登記為 park 的項目都沒有重複列：qa/overlay.png、storage 參數沒用到、`normalize_png`、檢視者 GET 會重算線稿、`_pick` 409 留下孤兒上傳、session 列越積越多等等。

---

### B1 [中] [安全] 上傳與 JSON body 在登入檢查之前就整個讀完，而且沒有大小上限
- 位置: backend/app/api/compose.py:496-501（read_upload）、compose.py:446、books.py:391；FastAPI 0.146 routing.py:451-470 先讀 body（`request.form()`／`request.body()`），之後才在 :511 `solve_dependencies`（current_owner／require_owned_path）
- 問題: 端點的 body 會在相依性（包括 `current_owner`）執行之前被 FastAPI 整個讀進來。multipart 的檔案部分在 Starlette 沒有大小上限，超過 1 MB 就寫到暫存檔。`read_upload` 的 10 MB 上限要等到 handler 才檢查，那時檔案早就整個落地了。JSON body 也是一樣，會整個讀進記憶體。整條鏈都沒有請求大小限制：uvicorn 沒設、Next rewrite 沒有 middleware/proxy，所以也沒有 `proxyClientMaxBodySize`。
- 會怎樣出錯: 沒有 cookie 的請求 `POST /api/pages/1/backgrounds/upload` 帶一個 20 GB 的檔案，會先把 /tmp 寫滿，最後才得到 401。對 `PUT /api/pages/1/lineart/edits` 送幾 GB 的 JSON，會先吃掉幾 GB 記憶體才回 401。前端服務綁 0.0.0.0:3000（deploy/coloring-books-frontend.service），所以只要能連到前端就能這樣做。
- 建議修法: 加一個 ASGI middleware，依 Content-Length 或邊讀邊累計：/upload 路由超過約 11 MB、其他 /api 超過約 2 MB 就回 413。或者在對外的反向代理設 body 上限。
- 把握: 可能（後端行為已經對照 FastAPI 原始碼確認。不確定的是 Next 16 的 rewrite proxy 在沒有 middleware 時會不會自己限制大小）

### B2 [中] [安全] dev_owner 開著時，任何網站都能以 admin 身分跨站送出「簡單請求」（CSRF）
- 位置: backend/app/api/deps.py:48-54、invites.py:429（`samesite="lax"`）；scripts/dev.env.example:35 預設 `APP_AUTH__DEV_OWNER=admin`
- 問題: 跨站 POST 時，瀏覽器依 SameSite=Lax 不會帶 cookie。後端看到「沒有 cookie」，就退回 dev_owner，把請求當成 admin，而 admin 看得到也改得到所有人的書。整個後端也不檢查 Origin／Referer。JSON 端點有 content-type 檢查擋著，但沒有 body、body 選填，或 multipart 的端點都是 CORS「簡單請求」，不需要 preflight 就會送出。
- 會怎樣出錯: 開發者用 dev.sh 時，在同一個瀏覽器開了惡意網頁。網頁用 `<form method=post enctype=multipart/form-data action="http://localhost:3000/api/books/3/character/upload">` 帶 `confirm_change=true` 就換掉第 3 本書的主角。用 `fetch(..., {method:"POST", mode:"no-cors"})` 打 `/api/books/{id}/produce`、`/materials/batch`、`/pages/{id}/backgrounds`、`/undo`、`/accept`、`/retry`，可以排滿 GPU（admin 沒有每日上限）或改動頁面。書號是連號，很好猜。README 只說 dev_owner 給區網用（已 park），沒提到網際網路上的網頁也能這樣做。
- 建議修法: dev_owner 這條後備路徑只接受 GET/HEAD，或要求 `Origin`／`Sec-Fetch-Site` 是同源。另外加一個全域 middleware：會改資料的請求如果帶了跨站 Origin 就回 403（cookie 使用者也一起受保護）。
- 把握: 確定（只有設定 dev_owner 時才會發生；正式的 service 檔沒有設）

### B3 [中] [死碼] `GET /generations/{id}/lineart-preview` 和一整套預覽機制已經沒有前端在用
- 位置: backend/app/api/lineart.py:284-304；services/page_lineart.py:268-283（ensure_preview／_preview）；storage.py:111、126（`preview` key builder）；api/pages.py:290-298（刪頁時清 cache/previews）；scripts/invites.py:272-285、296-300；scripts/storage_gc.py:59-60、265-295（預覽孤兒判斷）
- 問題: 線稿預覽原本給 CharacterStudio 的切換開關用（progress.md 寫「previews only from CharacterStudio toggle」），但 CharacterStudio 已經從前端移除了。現在這個端點只有後端測試會呼叫。即使如此，刪頁、delete-data、storage_gc 都還在維護 cache/previews/ 的清理和孤兒判斷，也各自有測試。
- 會怎樣出錯: 誰都沒呼叫。grep 過 `lineart-preview|lineartPreview|CharacterStudio|previewUrl` in frontend/src、frontend/e2e（非 test、非 src/api/openapi.json 的結果為 0），scripts/ 也是 0。只剩 tests/test_api_lineart.py、test_image_routes.py、test_storage_callers.py 用到。
- 建議修法: 刪掉端點、`ensure_preview`／`_preview`、`Storage.preview`，以及刪頁和 delete-data 的預覽清理。storage_gc 保留「cache/previews/ 底下全部是孤兒」的一次性清理就好（舊物件），之後再拿掉。重新產生 openapi.json／schema.d.ts。
- 把握: 確定

### B4 [中] [死碼] Phase 9 以前的欄位還在 API 合約裡：pose_mode／background_style 與「舊前端會送」的忽略欄位
- 位置: backend/app/api/schemas.py:182-209（PoseMode、BackgroundStyle、pose_mode_of、background_style_of）、:227-228（GenerationOut）、:166（CharacterCandidatesCreate.count）、:283（PageUpdate.controlnet_strength）、:305（PoseCreate.mode）、:308-311（BackgroundCreate.style）
- 問題: 每回傳一筆 GenerationOut，都要解析一次 request_json 來算 `pose_mode`／`background_style`，好讓前端在舊縮圖上標「快速／細緻」。現在前端已經沒有這個標示了，只剩 Thumbs.test.tsx 的假資料用到。四個請求欄位的註解都寫「舊前端會送，忽略」，但前端和後端是一起部署的，沒有舊前端存在：hooks.ts:265 明確 `Omit<..., "count">`，fixApi.ts:126-127 送 `{}` 和 `{action}`。這些欄位讓人誤以為後端支援模式切換，`count` 的 `ge=1, le=8` 也誤導人以為可以一次產生多張。
- 會怎樣出錯: 誰都沒呼叫。grep 過 `\.(pose_mode|background_style)\b|controlnet_strength|count:|mode:|style:` in frontend/src、frontend/e2e（排除 .test.、src/api/），非測試的程式碼裡 0 筆。
- 建議修法: 從 GenerationOut 拿掉 pose_mode／background_style 和兩個 helper，四個請求 model 改成預設 `extra="ignore"`（或維持 Pydantic 預設的忽略行為），刪掉這些欄位。重新產生前端型別。
- 把握: 確定

### B5 [低] [死碼] `GET /generations/{id}/mask` 與 `GenerationOut.mask_url` 沒人用
- 位置: backend/app/api/generations.py:92-109；schemas.py:225、257
- 問題: 前端只用 `cutout_url`（伺服器端已經把遮罩裁好），遮罩本身從來不直接抓。這個端點對每個請求都會做一次 HEAD 加簽章，卻沒有呼叫端。
- 會怎樣出錯: 誰都沒呼叫。grep 過 `/mask\b|mask_url` in frontend/src、frontend/e2e：只出現在 openapi.json 和 EditorPage／FixPage 測試的假資料。scripts/ 的結果是 0（experiments/results/phase5_*/run.py 是封存的實驗）。
- 建議修法: 刪掉端點和 mask_url（cutout_url 的條件 `gen.mask_path` 保留）。
- 把握: 確定

### B6 [低] [死碼] 一批回應欄位前端從不讀取，其中 BookStats.needs_fix 每次列書都要解析每一頁的 qa_json
- 位置: backend/app/api/books.py:75-81 與 schemas.py:76-77（BookStats.needs_fix／no_selection）；generations.py:49、288（QueueStatus.running_id）；schemas.py:97（SharedBookOut.cover_page_id）、:219、:221（GenerationOut.prompt／seed）、:60-70（LayoutOut 除了 content_px／generation_size 以外的 8 個欄位）、:492-494（QAOut.dangling_count／near_miss_count／metrics）
- 問題: 這些欄位只出現在前端測試的假資料。`book_outs` 每次 `GET /api/books` 都要對每本書的每一頁 `json.loads(qa_json)`，只為了算出沒人用的 `needs_fix`。`running_id` 對非 admin 永遠是 null，前端也不用它。
- 會怎樣出錯: 誰都沒呼叫。grep 過 `stats\.[a-z_]+` in frontend（非 test 的只有 pages、passed、ready、attention、working、todo），以及 `\.(prompt|seed|running_id|cover_page_id|metrics|dangling_count|near_miss_count)\b`、`page_px|trim_box_px|ratio_group…`，非測試的程式碼裡 0 筆。
- 建議修法: 刪掉這些欄位（至少 BookStats.needs_fix／no_selection 和 running_id），重新產生型別，並更新測試的假資料。
- 把握: 確定

### B7 [低] [bug] 合成圖從不會被捨棄，storage_gc 永遠不清，每拖一次位置就多一張整頁 PNG
- 位置: backend/app/services/page_compose.py:210-246（每個新的 placement 都 `store_outside_tx` 一筆 COMPOSITE）；scripts/storage_gc.py:4-10、166-179（只清 `discarded` 的）
- 問題: C 的「拖位置」「換背景」「復原」都會用新的輸入合成一張新的 COMPOSITE generation，舊的那張保留，以便之後「完全相同就直接選回」。但合成圖沒有任何流程會把它設成 discarded，storage_gc 只清「捨棄超過 30 天」和「沒有列指到」的物件，所以合成圖會一直留著。
- 會怎樣出錯: 一頁拖 50 次位置（每次 PUT placement?oneclick 都是一次新的 key），R2 裡就多 50 張整頁大小的合成 PNG，永遠不會被清掉。`list_generations` 回傳的列也跟著一直變長。
- 建議修法: storage_gc 加一條規則：不是選定圖、不在任何 PageHistory 快照可能重組出來的組合裡、而且超過 N 天的 COMPOSITE，清掉 image_path。或者每頁只保留最近 K 張。
- 把握: 確定（影響是儲存量和列表長度，不會丟資料）

### B8 [低] [bug] 分享的 PDF 只要有一頁讀不到線稿，整本就變成 404「找不到這本分享的繪本」
- 位置: backend/app/api/shared.py:159-163；export.py:350-353（`composed_page` 遇到 LineartUnavailable 或字型問題就丟 409）
- 問題: `book_pdf` 在任何一頁失敗時都丟 HTTPException 409，`shared_book_pdf` 再把它整個換成 404。擁有者那邊回 409「第 N 頁：…」是刻意的設計（T2 ruling），但分享端看起來就像整本書不見了，可是同一本書的檢視頁還是正常（壞掉的那一頁只是自己 404）。
- 會怎樣出錯: 擁有者有一頁的選定圖物件遺失，或伺服器的 caption 字型不能用。檢視者可以正常瀏覽這本書，按「下載 PDF」卻得到「找不到這本分享的繪本」。
- 建議修法: 分享 PDF 略過讀不到的頁，只輸出能組出來的頁（全部失敗才 404）。或者至少回一個不同的訊息，例如「這本書暫時無法下載」。
- 把握: 確定

### B9 [低] [安全] `invites.py revoke` 不會取消被撤銷者已經排隊的生成
- 位置: scripts/invites.py:120-129
- 問題: revoke 只設 `revoked_at` 並刪掉 session。這個 owner 排隊中（QUEUED）的 generation 和 PageTask 都留著，worker 會照常執行，佔用唯一一張 GPU，也照常寫進 storage。delete-data 有取消排隊的步驟，revoke 卻沒有。
- 會怎樣出錯: 發現某個邀請被濫用（例如一次排滿 60 張），撤銷之後 GPU 還是會花約 22 分鐘（60 張 × 每張約 22 秒）把它們跑完，其他人只能排在後面。
- 建議修法: revoke 用同一個交易，把 `book_id in (該 owner 的書)` 而且是 QUEUED 的 generation 改成 CANCELLED 並 `refund_generations`，排隊中的 PageTask 也一起取消。做法和 delete-data:215-222 一樣。
- 把握: 確定

### B10 [低] [安全] 登入 CSRF：任何人都能讓別人的瀏覽器登入成自己的帳號
- 位置: backend/app/api/invites.py:416-432
- 問題: `/i/{token}` 是 GET。跨站的頂層導覽在 SameSite=Lax 下照樣會寫入 cookie，而且會直接覆蓋原本的 cb_session。只要有一個有效的邀請 token，就能用 `<a href>` 或自動導向，讓受害者的瀏覽器登入成攻擊者的 owner。
- 會怎樣出錯: 受邀者 A 傳一個看起來無害的連結給 B，B 一點開就變成 A 的身分。B 之後畫的書全部存到 A 的帳號，A 看得到也拿得走。B 自己的書會「消失」，直到 B 重新打開自己的邀請連結。
- 建議修法: `/i/{token}` 改成顯示一個確認頁（「以『名字』登入？」），用 POST（同源）完成登入。或者已經有有效 cookie、而且是不同 owner 時，先問一次再覆蓋。
- 把握: 確定（試用只有 5–10 個互相信任的人，實際風險低）

### B11 [低] [bug] 允許 `signed_url_ttl_s` 小於 302 的快取時間（60 秒），會轉到已經過期的網址
- 位置: backend/app/config.py:81（`Field(120, gt=0)`）；backend/app/api/files.py:369-376
- 問題: 圖片端點的 302 帶 `Cache-Control: private, max-age=60`，瀏覽器在 60 秒內會重用同一個簽章網址。設定只要求 TTL 大於 0，沒有要求它大於 60 秒再留一點餘裕。
- 會怎樣出錯: 把 `storage.signed_url_ttl_s` 設成 30（例如想縮短網址的有效時間）。第 31–60 秒之間重新顯示同一張圖時，瀏覽器沿用快取的 302，R2 或 /api/files 回 403，畫面上的圖會破掉。
- 建議修法: 設定驗證改成 `ge=REDIRECT_MAX_AGE + 30`；或者讓 `REDIRECT_CACHE_CONTROL` 的 max-age 取 `min(60, ttl // 2)`。
- 把握: 確定（預設值 120 不會出問題）

### B12 [低] [死碼] storage_gc 的快照欄位清單裡有兩個快照根本不會有的 key
- 位置: scripts/storage_gc.py:124-125；對照 services/history.py:74-85（snapshot_of）
- 問題: `_SNAPSHOT_GEN_FIELDS` 列了 `selected_generation_id` 和 `composite_generation_id`，但 `snapshot_of` 只寫 background／pose（加上 placement、文字、筆畫）。讀的人會以為快照會固定合成圖，進而誤判 B7 的合成圖已經受到保護。
- 會怎樣出錯: 誰都沒寫入。grep 過 `composite_generation_id` 在整個 repo 的結果：只有 storage_gc.py:125 這一處。
- 建議修法: 只留 background／pose 兩個欄位，並加註解說明快照不固定合成圖。
- 把握: 確定

### B13 [低] [結構] compose.py 實際上是排隊服務層，其他路由模組 import 它的私有函式，還要靠延遲 import 繞開循環
- 位置: backend/app/api/compose.py:85-360（`_Job`、`_add_queued`、`_enqueue_jobs`、`_BookState`、`_background_job`、`_pose_job`、`_take`、`_BATCH_LOCK`、`read_upload`、`_mark_stored`）；history.py:309、produce.py:24、books.py:403（函式內 import）、books.py:49-50（`book_outs` 函式內 import pages、produce）
- 問題: 約 600 行的路由模組裡，一半是給 history、produce、books 共用的生成任務組裝、批次規劃、上傳驗證。其他模組 import 帶底線的私有名稱，books↔compose↔pages↔produce 之間的循環要靠函式內 import 繞開。`_BATCH_LOCK` 是行程內的鎖，卻同時保護 compose、history、produce 三個模組的語意。
- 會怎樣出錯: 結構問題，不會直接出錯。但改一個「私有」函式的簽名會悄悄弄壞另外三個模組；新增端點時如果 import 順序不對，就會撞上循環 import。
- 建議修法: 把 `_Job`…`_take`、`_BookState`、`has_character`、`build_job`、`_BATCH_LOCK` 搬到 `services/jobs.py`，`read_upload`／`_mark_stored` 搬到 `services/uploads.py`（和 upload_bg 合併），`book_outs` 依賴的 `busy_page_ids`／`page_state` 搬到 services。這樣路由模組只負責 HTTP。
- 把握: 確定

### B14 [低] [結構] 請求／回應 model 分散在路由模組裡，schemas.py 反而放了業務邏輯
- 位置: backend/app/api/generations.py:28-52（GenerationUpdate、PendingJob、RecentJob、QueueStatus）；review.py:183-199（CharacterCheckIn、PassIn）；schemas.py:182-209、330-331、385-391（從 request_json 推斷模式、lineart_url 網址組法、needs_fix 判斷）
- 問題: 約 615 行的 schemas.py 裡有 legacy 推斷和網址規則（produce.py:210 用 `lineart_url()` 來比對「線稿是不是最新」），但有 6 個 model 卻定義在路由檔裡。要找某個 API 的形狀得翻好幾個檔案，網址格式也同時散在 schemas 和 overview。
- 會怎樣出錯: 結構問題，不會直接出錯。改 lineart 網址格式時，很容易漏掉 produce.py:210 的 `fresh` 判斷，結果是 gap_count 永遠顯示 null。
- 建議修法: 把 model 都集中到 schemas.py（或依領域拆成 schemas/ 套件）。網址組法和 legacy 推斷搬到 services 或專用的 `urls.py`。
- 把握: 確定

### B15 [低] [死碼] 重複的小工具散在各模組
- 位置: `_out(gen, worker)` 在 compose.py:85 和 generations.py:56，books.py:306-310 又內嵌了一份；「上一個變化語」掃描寫了三次，在 books.py:231、compose.py:140、compose.py:158；compose.py:378-384 的 create_pose 內嵌了 `has_character`（同檔 :295）的同一段邏輯；shared.py:29-39 的 `_bounded_ids` 重寫了 deps.py:133-141 的 id 範圍檢查；`NO_SELECTION` 定義在 history.py:376、lineart.py:63、review.py:186，export.py:424 又寫死一次；admin.py:465 `DUPLICATE_NAME = auth.DUPLICATE_NAME` 只是別名，而且 create_invite:530-534 的預先查重和 `create_owner` 裡面的檢查重複
- 問題: 同一件事有多份實作。以 id 範圍檢查為例，只要有一份改了上下界（例如 0 或負數 id），另一份不會跟著改。
- 會怎樣出錯: 改一處漏另一處。例如 create_pose 的條件和 `has_character` 不同步時，批次會略過「請先設定角色」，單張卻會排進一筆一定失敗的任務。
- 建議修法: 合併成一份：`GenerationOut.for_worker(gen, worker)`、一個 `last_variations(session, where, n)`、create_pose 直接呼叫 `has_character`、把 deps 的 id 解析抽成 `parse_db_id()` 讓 shared 共用，訊息常數集中在一處。
- 把握: 確定

### B16 [低] [結構] 誤導的名稱與過時的註解
- 位置: backend/app/services/upload_bg.py（名字叫 upload_bg，裡面的 `letterbox` 卻是給「上傳主角」用的）；compose.py:74、499（`MAX_BG_UPLOAD_BYTES` 也套用在主角上傳）；files.py:369、396（`REDIRECT_CACHE_CONTROL` 也用在 /api/files 的檔案內容本身）；storage.py:179（LocalStorage 寫「測試與假後端用」，但它是 `storage.backend: local` 的正式後端，也是設定範本的預設值）；schemas.py:283、305、311 與 compose.py:209、374（「舊前端會送」：不存在的舊前端，見 B4）
- 問題: 這些名稱和註解描述的用途和實際用途不一樣，讀的人會誤判它影響的範圍。例如以為改 `MAX_BG_UPLOAD_BYTES` 只影響背景上傳，或以為 LocalStorage 不需要像正式後端那樣小心處理。
- 會怎樣出錯: 有人只想放寬背景上傳的上限而改了 `MAX_BG_UPLOAD_BYTES`，結果主角上傳的上限也一起變了；或者因為註解說「測試用」，就放鬆 LocalStorage 的檢查。
- 建議修法: 改名為 `services/upload_image.py`、`MAX_UPLOAD_BYTES`、`SIGNED_CACHE_CONTROL`，更新 LocalStorage 的 docstring，並刪掉「舊前端」的註解（和 B4 一起處理）。
- 把握: 確定

---

統計：高 0、中 4、低 12。依類別：安全 4（B1、B2、B9、B10）、bug 3（B7、B8、B11）、死碼 6（B3、B4、B5、B6、B12、B15）、結構 3（B13、B14、B16）。
