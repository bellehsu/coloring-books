# Area A：後端生成核心與資料模型 — 程式碼審查

審查基準：HEAD `3d9e0b8`。審查期間工作區出現未提交的修改（`backend/app/services/history.py` 新增 `has_both_slots`／`can_compose_after`、
`take_generation` 只在另一格有圖時才設 pending；`api/schemas.py` 的 `recompose_pending` 改看 `has_both_slots`；`tests/test_api_history.py`），
看起來是有人正在修「缺另一格時 C 一直顯示製作中」。那部分不在本報告範圍，也沒有重複回報。
已排除 `.superpowers/sdd/*/progress.md` 已記錄並擱置的項目（M2 finish_page_task 收尾失敗、M4 on_generation_succeeded 失敗只記 log、
M5 快照在鎖之前讀取、R2 暫時錯誤直接失敗、啟動時 R2 阻塞等）。

---

### A1 [中] [bug] ComfyUI 的 HTTP 非 200 一律當成「連不上」，任務會無上限地重排、整個佇列被一筆毒任務卡住
- 位置: backend/app/services/comfy_client.py:157-160, 183-184, 211-212, 258-259；backend/app/services/comfy_engine.py:263-264；backend/app/services/worker.py:338-341
- 問題: `_get_json`、`list_models`、`upload_image`、`download` 收到任何非 200 都丟 `ComfyConnectionError`，引擎把它轉成 `EngineUnavailable`，worker 就把這筆放回 QUEUED，而且沒有次數上限。`_claim` 永遠先拿最舊的 QUEUED，所以下一輪拿到的還是同一筆。ComfyUI 在執行中崩潰也走同一條路：WS 斷線，改用輪詢，輪詢連不上，丟 `ComfyConnectionError`。
- 會怎樣出錯: (a) `/view` 下載輸出回 404/500，或經反向代理上傳角色圖回 413：GPU 已經跑完，結果被丟掉，10 秒後同一筆重跑，結果一樣，就這樣無限循環。整個全站單一佇列卡在這一筆，所有使用者的任務都不會動，GPU 一直空轉。(b) 某個輸入讓 ComfyUI 程序崩潰：ComfyUI 重啟後同一筆又被送進去，崩潰就一再重演。
- 建議修法: 只有連線層錯誤（`httpx.ConnectError`、WS 連不上）才算 `ComfyConnectionError`；HTTP 4xx/5xx 改成一般 `ComfyError`，讓任務失敗。另外在 request_json 或新欄位記錄「因引擎不可用而重排的次數」，超過 N 次（例如 3 次）就標成 FAILED，並退回額度。
- 把握: 確定（程式路徑）；實際觸發要有確定性的 HTTP 錯誤或崩潰。

### A2 [中] [bug] 連線逾時被當成「生成失敗」：ComfyUI 主機不可達時整個佇列一筆筆變 FAILED，每頁都被標「請看一下」
- 位置: backend/app/services/comfy_client.py:152-153, 57-58；backend/app/services/comfy_engine.py:263-266
- 問題: `_request` 先攔 `httpx.TimeoutException`，`httpx.ConnectTimeout` 也是它的子類別，所以丟出 `ComfyTimeoutError`。`ComfyTimeoutError` 只是 `ComfyError`，不是 `ComfyConnectionError`，引擎就把它轉成不可重試的 `EngineError`。只有「連線被拒」才會走等待重試的 ENGINE_UNAVAILABLE。
- 會怎樣出錯: ComfyUI 在另一台機器上（雲端路線、WSL 網路斷掉、主機睡眠，封包被丟掉而不是被拒絕）：每筆任務先等 WS 10 秒、POST /prompt 30 秒，然後 FAILED。佇列裡每一筆依序失敗並退回額度，`on_generation_failed` 把每頁標成 attention="failed"。使用者看到整本書都「請看一下」，只能一頁頁「再試一次」。
- 建議修法: 在 `_request` 把 `httpx.ConnectTimeout`（以及 `PoolTimeout`）歸成 `ComfyConnectionError`。只有讀取逾時才用 `ComfyTimeoutError`。
- 把握: 確定

### A3 [中] [bug] 額外候選圖生成失敗也會把「可以印」的頁標成請看一下，預設的「印出這本書」因此少了這頁
- 位置: backend/app/services/pipeline.py:225-234（`on_generation_failed`）；backend/app/services/worker.py:351-357；影響 backend/app/api/export.py:52-58（include=ready）
- 問題: 任何綁在頁面上的背景／動作生成只要 FAILED，就無條件寫 `attention="failed"`。程式不看這筆是不是頁面正在等的圖，也不看頁面是否已 PASSED、目前選用的圖是否完好，也不看這筆有沒有在執行中被捨棄。attention 只有頁面流程 PASSED 或「這頁可以印了」才會清掉。
- 會怎樣出錯: 頁面已經 PASSED。使用者在進階編輯按「再產生一張」背景（非 take），這張因逾時或 OOM 失敗：B 顯示「請看一下」，`include=ready` 的 PDF 與 `ready_ids` 都不含這頁，但這頁目前的圖完全能印。在 C 按「AI 再畫一張」失敗，或使用者把執行中的候選捨棄後它失敗，也一樣。
- 建議修法: 只在「這頁還在等這一格」時設 attention，也就是該格是空的，或這筆是 take 而頁面不是 PASSED。或者 attention 用另一個值（例如 "gen_failed"），export 的 ready 不排除它。被捨棄的生成不要設 attention。
- 把握: 可能。spec（2026-11-11-oneclick-book-design.md:94）寫明「A failed generation also sets attention」，但沒有談到已 PASSED 的頁被排除在列印之外是否符合預期，需要使用者確認。

### A4 [低] [bug] worker 收尾寫入失敗沒有重試，生成列會一直停在 RUNNING 直到重啟
- 位置: backend/app/services/worker.py:270-282（`_finish`）, 319-335, 338-346, 351-352
- 問題: `_finish` 成功或失敗的 UPDATE 若丟例外（例如 SQLite 鎖超過 30 秒 busy timeout、磁碟錯誤），會進入 `except Exception`，接著呼叫 `_fail`。`_fail` 再寫一次 DB，很可能同樣失敗，例外就冒到 `_loop`，只記 log 後繼續下一筆。頁面流程那邊已經有「收尾失敗再試一次」的迴圈（`_run_page_task` 394-400），生成這邊沒有。
- 會怎樣出錯: 這一列留在 RUNNING，`storage` 已經寫了圖但列沒有記到。直到後端重啟之前：`queue_position` 一直回 0；`delete_page` 一直回 409「還有圖片正在生成」；B 的進度一直顯示製作中；`storage_gc` 也把它當作寫入中。重啟後 `requeue_interrupted` 把它放回 QUEUED，又重跑一次 GPU。
- 建議修法: 比照 `_run_page_task`，`_finish`／`_fail` 失敗時退避重試幾次。仍失敗就把 gen_id 記在記憶體，下一輪迴圈開始前先補寫。
- 把握: 確定（程式路徑）；觸發要 DB 長時間鎖住，機率低。

### A5 [低] [bug] 後端停止時不中斷 ComfyUI 正在跑的 prompt，重啟後同一筆再跑一次
- 位置: backend/app/services/worker.py:152-162（`stop`）, 178-193（`requeue_interrupted`）
- 問題: `stop()` 只取消 asyncio task，ComfyUI 端的 prompt 繼續執行。重啟時這筆從 RUNNING 放回 QUEUED，重新送出。
- 會怎樣出錯: 開發時常重啟（dev.sh、改程式自動重載）。每次在生成中途重啟，舊 prompt 仍佔用 GPU 跑完，新送的同一筆排在後面，白白耗掉一次生成的時間。
- 建議修法: `stop()` 取消前若有執行中的生成，盡力呼叫 `ComfyClient.interrupt()`（設短逾時，失敗忽略）。
- 把握: 確定

### A6 [低] [bug] 角色參考圖的上傳快取永遠有效，ComfyUI 的 input 被清掉後所有動作生成都會失敗
- 位置: backend/app/services/comfy_engine.py:251-258
- 問題: `_upload_cache` 是行程內的 dict，只要內容 hash 一樣就不再上傳。ComfyUI 那邊的檔案如果不見了（清了 input 資料夾、換機器、雲端暫時容器重開），快取不會失效。preflight 的 reload 也只換 registry，不清這個快取。
- 會怎樣出錯: ComfyUI 回 `ComfyPromptError`（LoadImage 找不到檔），轉成 `EngineError`。同一本書之後的每個角色動作都 FAILED，每頁都被標「請看一下」，要重啟後端才恢復。
- 建議修法: 收到含 LoadImage 錯誤的 `ComfyPromptError` 時清掉該項快取，重新上傳後重試一次。或者乾脆每次都上傳（檔案約 1–2 MB，overwrite=True 本來就是冪等的）。
- 把握: 可能（本機 ComfyUI 的 input 資料夾會保留；雲端路線才容易觸發）

### A7 [低] [安全] ComfyUI 連不上時，每個受邀使用者都看得到內部 ComfyUI 位址與錯誤原文
- 位置: backend/app/services/worker.py:338-341；backend/app/services/comfy_engine.py:263-264；backend/app/services/comfy_client.py:155；backend/app/api/generations.py:283-286
- 問題: 進入 ENGINE_UNAVAILABLE 時，`last_error` 是「ComfyUI 無法連線：無法連線 ComfyUI（{base_url}）：{httpx 例外}」。`/api/system/queue` 在 `system_error` 時對非管理者也回傳它。
- 會怎樣出錯: 雲端部署時，任何持有邀請連結的人都能看到內部推論端點的網址與連線細節。
- 建議修法: 非管理者只回固定的中文訊息（例如「繪圖服務暫時無法連線」），原文只給 admin。
- 把握: 確定

### A8 [低] [結構] preflight 宣稱「任何例外都轉成報告」，實際上有幾條路會變成 500
- 位置: backend/app/services/preflight.py:12, 178-185（`list_models` 的 `resp.json()`）, 329-333；backend/app/services/comfy_client.py:185；backend/app/main.py:507-513
- 問題: `run_preflight` 對每個 workflow 只攔 `ComfyError`。`list_models` 的 `resp.json()` 遇到非 JSON 會丟 `JSONDecodeError`；`_check_inputs` 遇到格式怪的 `/object_info` 會丟 AttributeError／TypeError；這些都直接冒出去。另外 `GET /api/system/preflight?reload=true` 的 `WorkflowRegistry.load()` 若 workflows_dir 被刪掉會丟 FileNotFoundError。
- 會怎樣出錯: 管理者打開檢查頁時拿到 500，看不到缺漏清單。CLAUDE.md 要求檢查失敗時回報清楚的缺漏清單。啟動時這條路有外層 try，不會讓服務當機。
- 建議修法: 每個 workflow 的檢查用 `except Exception` 包起來，寫進 `load_errors`。`list_models` 的 JSON 錯誤轉成 `ComfyConnectionError`。reload 失敗時回報到報告裡，並保留舊的 registry。
- 把握: 確定

### A9 [低] [死碼] `app.state.preflight` 快取只寫不讀
- 位置: backend/app/main.py:5, 72, 144
- 問題: 快取在啟動與每次 GET 時更新，但沒有任何程式讀它（docstring 也說生成請求不看）。測試只驗證「設了它也不影響排隊」（tests/test_api_compose.py:193,305,454；tests/test_scripts.py:491,505）。
- 會怎樣出錯: 誰都沒有讀它。grep `state.preflight` 搜過 backend/app、frontend/src、scripts，只有 main.py 寫入。讀者會以為有地方依賴這份快取。
- 建議修法: 刪掉這個快取與相關 docstring。測試改成直接驗證「排隊不呼叫 preflight」（test_scripts 的 `seen == []` 已經涵蓋）。
- 把握: 確定

### A10 [低] [死碼] `history.can_undo_redo` 沒有人呼叫，`history_flags` 另外寫了一份相同的邏輯
- 位置: backend/app/services/history.py:145-148（對照 151-169）
- 問題: 兩份「可復原／可重做」判斷並存，只有 `history_flags` 被用到。
- 會怎樣出錯: 誰都沒有呼叫它。grep `can_undo_redo` 搜過 backend、tests、scripts，只有定義。日後只改其中一份，兩者就會不一致。
- 建議修法: 刪除 `can_undo_redo`，或讓 `history_flags` 用它來算。
- 把握: 確定

### A11 [低] [死碼] 生成核心殘留沒有人用的介面：`ComfyClient.model_folders`、`ComfySettings.ws_url`、`GenerationRequest.negative_prompt`
- 位置: backend/app/services/comfy_client.py:175-176；backend/app/config.py:246-248；backend/app/services/engine.py:20、worker.py:111
- 問題: `model_folders` 沒有呼叫者。`ComfySettings.ws_url` 只有 tests/test_config.py:12 用到，真正的 WS 位址由 `ComfyClient.ws_url` 產生，而且格式不同（後者帶 clientId）。`negative_prompt` 從 request_json 解出來放進 `GenerationRequest`，但 `ComfyEngine` 從不讀它（Qwen cfg=1，新列一律是空字串）。
- 會怎樣出錯: grep `model_folders` 搜過 backend、scripts、tests、experiments，只有定義。grep `\.ws_url` 在後端只有 `ComfyClient` 自己的屬性。grep `\.negative_prompt` 在 services 只有 dataclass 欄位，引擎與 fake engine 都不讀。兩個 ws_url 容易讓人改錯地方。
- 建議修法: 刪掉 `model_folders` 與 `ComfySettings.ws_url`（連同那條測試）。`GenerationRequest.negative_prompt` 移除，DB 欄位保留給舊資料。
- 把握: 確定

### A12 [低] [結構] 後端啟動強制檢查 `comfy.models_root`／`install_dir` 存在，但後端程式完全不讀這兩個設定
- 位置: backend/app/config.py:233-234, 404-410；backend/app/main.py:408（`load_settings()` 預設 check_paths=True）
- 問題: 這兩個路徑只有 `scripts/sync_comfy_model_paths.py` 用到，backend/app 內 grep `models_root|install_dir` 除了 config.py 沒有其他結果。但後端啟動時 `validate_paths` 要求它們存在，否則 `SettingsError` 讓服務起不來。
- 會怎樣出錯: ComfyUI 在另一台機器上（雲端路線），或模型放在後端看不到的磁碟時，後端必須建一個假的 models_root 資料夾才能啟動。錯誤訊息還會誤導人以為後端需要模型。
- 建議修法: 把 ComfyUI 本機路徑的檢查移到 sync 腳本與 preflight。`models_root` 改成選填，後端的 `validate_paths` 只檢查 workflows_dir、storage_dir、database。
- 把握: 確定

### A13 [低] [結構] 「最新一張可用的 AI 圖（不含上傳）」與引擎名稱字面值分散在多處，常數放在錯的模組
- 位置: backend/app/services/pipeline.py:240-241, 259-263；backend/app/services/history.py:46-50, 296-301；backend/app/services/worker.py:44；backend/app/api/schemas.py:203,261；backend/app/api/generations.py:276
- 問題: 排除上傳圖的條件在 `pipeline._auto_candidates` 與 `history.auto_target` 各寫一次，查詢本身也重複。`"upload"`／`"compose"`／`"comfyui"` 在 history、worker、schemas、generations、page_compose 各自寫字面值。`SLOTS`、`UPLOAD_ENGINE` 定義在 history.py，再由 pipeline 轉出口給 API 用。
- 會怎樣出錯: 日後新增一種本地引擎（例如雲端 diffusers 的 engine 名稱）或改上傳的標記時，很容易漏改其中一處，造成自動選圖選到上傳圖，或 ETA／佇列把它算成 GPU 任務。
- 建議修法: 在 models.py 或一個小的 `generation_kinds.py` 集中定義引擎名稱常數與 `SLOTS`。「最新可用 AI 圖」抽成一個函式，讓 `auto_select` 與 `auto_target` 共用。
- 把握: 確定

### A14 [低] [結構] `encode_request` 的說明與實際用途相反：extra 不只是來源紀錄，還承載流程與帳務旗標
- 位置: backend/app/services/worker.py:53-87（docstring 70-71）；backend/app/services/pipeline.py:63, 195-200；backend/app/services/usage.py:7-9, 145-159
- 問題: docstring 說 extra「只當來源紀錄（例如 pose_mode、background_style），引擎不讀」。實際上 `take` 決定生成成功後要不要強制換圖；`usage_day`、`usage_owner`、`usage_refunded` 是退額度的依據，會被 `refund_generations` 改寫。請求的序列化／反序列化與「已停用路線」判斷也都放在 worker.py，API 模組要從 worker import `encode_request`。
- 會怎樣出錯: 維護者看 docstring 會以為 extra 可以隨意增刪，或在別處重建 request_json（例如「重跑」功能），結果遺失 `usage_*`，造成不退額度或重複退；遺失 `take`，C 的換圖就不會換上去。`_CORE_KEYS` 也沒有保護這些控制欄位。
- 建議修法: 修正 docstring，並把 `take`、`usage_*` 列為受保護的控制欄位。把 encode/decode 與 `_is_retired` 移到獨立模組（例如 `services/gen_request.py`）。
- 把握: 確定

### A15 [低] [結構] 列生成清單時，每一筆都開一個新 session 查排隊位置
- 位置: backend/app/services/worker.py:225-250（`queue_position`、`_plan`）；backend/app/api/generations.py:56-59, 160；compose.py:85-87；books.py:308
- 問題: `queue_position` 每次呼叫都開新 session 並 `s.get`。是 QUEUED 時還會讀出整個全站佇列（`_plan`），再加一次 RUNNING 查詢。清單端點對每筆生成呼叫一次。
- 會怎樣出錯: 一本書累積數百筆生成時，一次 `GET /books/{id}/generations` 就開數百個 session。一鍵做書時佇列有幾十筆，每個 QUEUED 項目各掃一次全站佇列，成本是 O(N×Q)，而前端會輪詢這個端點。
- 建議修法: 提供批次版 `queue_positions(ids)`：一次算出 `_plan` 與 RUNNING，再對每筆查表。非 QUEUED／RUNNING 的直接回 None，不必再開 session。
- 把握: 確定（程式路徑）；實際延遲沒有量測。

### A16 [低] [結構] `page_task` 只增不減，B 的總覽每次輪詢都載入一本書全部的歷史工作
- 位置: backend/app/models.py:230-242；backend/app/services/pipeline.py:120-139；backend/app/api/produce.py:89-92, 197-203
- 問題: C 的每次拖拉、復原、改筆畫都會排一筆 page_task。SUCCEEDED／FAILED 的列除了刪頁之外永遠不清，`_tasks()` 卻把整本書所有工作讀進 Python 才過濾。
- 會怎樣出錯: 試用期間一本 20 頁的書被反覆編修，task 表就會累積幾千列。總覽與「開始做這本書」每次都全部載入，回應越來越慢。
- 建議修法: 總覽只查 active 狀態，以及 `created_at >= produce_started_at` 的列（用 SQL 篩選與計數）。另外定期，或在 `finish_page_task` 時，刪掉每頁較舊的已完成工作。
- 把握: 確定（程式路徑）；增長速度是推估的。

### A17 [低] [結構] worker 的同步 DB 寫入與遮罩處理直接在 event loop 上執行
- 位置: backend/app/services/worker.py:286, 316-319（`largest_component`）, 319-335, 351-367；backend/app/db.py:283
- 問題: storage 讀寫已經用 `asyncio.to_thread`，但 `_claim`、`_finish`、`_fail`、`_after_success`（含 `take_generation`、`auto_select`）與 `largest_component`（整張 832×1216 遮罩做連通區塊）都在 event loop 上同步執行。SQLite busy timeout 是 30 秒。
- 會怎樣出錯: 某個 API 交易持有寫入鎖（例如大量刪除、匯入）時，worker 的 `_finish` 在 event loop 上最多等 30 秒。這段時間 uvicorn 無法派送任何請求，連靜態的 `/api/files` 轉址都卡住，全站看起來像當機。
- 建議修法: 把 `_finish`、`_fail`、`_after_success`、`_claim` 與 `largest_component` 一律包進 `asyncio.to_thread`（它們本來就各自開 session，執行緒安全）。
- 把握: 可能（要有長時間持有寫入鎖的請求才會明顯；目前寫入交易大多很短）
