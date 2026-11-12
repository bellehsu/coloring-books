# Area C 審查：確定性影像流程、匯出、開發／維運腳本

範圍：backend/app/services/{lineart_extract,lineart_post,lineart_render,page_lineart,autofix,qa,compose,page_compose,captions,caption_text,export,layout}.py、backend/app/api/{lineart,export}.py、scripts/{preflight,check_env,sync_comfy_model_paths,dev.sh,export_openapi,dev_fake_backend}.py。
已讀 .superpowers/sdd/*/progress.md 的 parked／minor 項目。以下不再重報：qa/overlay.png 沒有人用、hole fill 會把圍起來的背景補白、preview 舊版本外洩、viewer GET 會懶重算線稿、標題縮字迴圈 <8 的下限到不了、book_pdf 單頁 409 讓整本失敗。

驗證方式：用 venv 對純函式跑小型 python 檢查：各紙張與模式的 compute_layout／band_box、標題字塊的邊界、筆畫錯位的數學、render/QA/apply_strokes 的耗時，以及全白和全黑圖。沒有跑完整測試，沒有碰 storage/ 或 dev.env。

---

### C1 [中] [bug] 修線筆畫用「內容框相對座標」存放，改列印方式或紙張後與線稿錯位最多約 50 px
- 位置: backend/app/services/lineart_render.py:54（`round(x * (w - 1))`，w＝content_px）；backend/app/services/page_lineart.py:182、241；backend/app/api/books.py:182-183（可以改 print_mode／同比例紙張）
- 問題: 線稿是把合成圖（背景的生成尺寸，例如 832×1216）以 `fit=contain` 放進 content_px，user／auto 筆畫卻存成相對於整個 content 框的 0–1 座標。內容框的長寬比一變，contain 留的白邊就跟著變，同一個相對座標就對不到原本的線。合成圖不會因為改模式而重做（背景還是舊的生成尺寸），所以白邊差異會一直存在。
- 會怎樣出錯: 實測 A4 家用（2244×3272，圖幾乎填滿）改成 A4 印刷廠（2551×3579，左右各約 51 px 白邊）後，靠近圖邊的筆畫最多偏 43.9 px；印刷廠改家用偏 51.3 px；A4 改 A5（同為家用）偏 15.3 px。線寬只有約 11 px，所以使用者的橡皮擦擦不到原本那條線，畫筆補的線浮在旁邊。舊編輯器頁面的 auto 補線（橋）也會變成亂畫的線段，QA 會因此出現新缺口、頁面退回草稿。頁面流程只重算 auto 層，user 層一樣錯位。
- 建議修法: 筆畫座標改成相對「來源圖在內容框中的位置（art box）」。或在 layout 改變時依新舊 art box 換算既有筆畫，也可以讓 lineart_key 改變時把筆畫轉換成新座標。至少在改模式或紙張時提示「修線會偏移」。
- 把握: 確定（以 resize_to_target 的 contain 公式與 apply_strokes 的映射算出偏移量；沒有在 UI 上實際操作）

### C2 [中] [死碼] lineart_extract 的 none／luma／color_edges／hybrid 四種方法只剩實驗在用
- 位置: backend/app/services/lineart_extract.py:6-20（docstring）、32-44（METHODS）、75-100（驗證）、117-170（_luma／_color_edges／_hybrid）
- 問題: 產品只會走 `render_base` → `extract(color, {"method": "xdog", "sigma": ...})`（lineart_render.py:41）。另外四種方法、它們的參數白名單和驗證，加上 docstring 裡「適合…」的說明，大約 120 行都是 Phase 1 Route C 的實驗工具。模組開頭的說明會讓人以為產品還在選方法。
- 會怎樣出錯: 產品裡誰都沒呼叫。grep `color_edges|hybrid|"luma"|lineart_extract` 搜了 backend、frontend/src、scripts、tests、experiments：除了本檔，只有 lineart_render.py（只用 xdog）、experiments/run_matrix.py（Phase 1 矩陣實驗，README 說要用 archive workflows 才跑得動）、tests/test_lineart_extract.py、tests/test_run_matrix.py。
- 建議修法: 把非 xdog 的方法搬到 experiments/（或 archive/），產品模組只留 `_rgb`／`_xdog`／`extract_xdog(sigma)`。docstring 改成描述目前的唯一路線。
- 把握: 確定

### C3 [低] [bug] compute_layout 用 `is` 比較列印模式，傳入字串 "home" 會靜悄悄變成印刷廠版面
- 位置: backend/app/services/layout.py:70、79（`if mode is PrintMode.HOME`）
- 問題: PaperSize 和 PrintMode 都是 `str, Enum`，`PAPER_MM[size]` 收字串也能查到，但模式判斷用的是身分比較。實測 `compute_layout('a4','home').page_px == (2551, 3579)`（印刷廠尺寸），用 enum 則是 (2480, 3508)。
- 會怎樣出錯: 目前產品的呼叫端拿到的都是 enum（DB Enum 欄位、pydantic body），所以還沒發生。只要哪天有呼叫端傳字串（實驗腳本、從 JSON 或設定讀進來、測試），家用模式就會印出含出血的尺寸，沒有任何錯誤。
- 建議修法: 開頭加 `mode = PrintMode(mode); size = PaperSize(size)`，或改用 `==`。
- 把握: 確定（目前是潛在問題）

### C4 [低] [bug] QA 對空白（或幾乎沒線）的線稿不報任何錯誤或警告
- 位置: backend/app/services/qa.py:265-272
- 問題: QA 只檢查缺口、黑塊、過小區域和碎片，沒有「線太少」的下限。實測全白、全黑、全灰（128）的彩色圖經過 render_base 後 ink 全空，`evaluate` 回傳 errors=[]、warnings=[]。
- 會怎樣出錯: 如果合成圖幾乎沒有亮度變化（xdog 抓不到線，例如上傳很淡的背景，角色又出問題），頁面流程會把空白頁標成「可以印」，匯出 PDF 裡就多一張白紙。機率低，但流程是全自動的。
- 建議修法: 加一個 `black_pixel_ratio < 某值`（例如 0.2%）或 `closed_region_count == 0` 的錯誤（或警告），讓這種頁進入「請看一下」。
- 把握: 確定（evaluate 的行為已驗證；實際發生的機率沒有資料）

### C5 [低] [安全] 合成時角色寬度沒有上限，遮罩外框極扁時會配置巨大影像
- 位置: backend/app/services/compose.py:139（`target_w = round(pose_rgb.width * target_h / pose_rgb.height)`）
- 問題: 角色高度以背景高度乘 placement.height 決定（最高約 1216 px），寬度照遮罩外框的長寬比放大，而且沒有上限。如果 largest_component 留下的最大區塊是一條很扁的帶狀（例如 832×1），target_w 會是幾十萬到上百萬 px，Pillow 的 LANCZOS resize 要配置好幾 GB。
- 會怎樣出錯: 合成在 FastAPI process 裡執行（頁面流程 worker 或確認合成 API），OOM 會讓整個後端被殺掉。BiRefNet 正常不會輸出這種遮罩，所以只是韌性問題。
- 建議修法: 把 target_w 限制在例如背景寬的 2–3 倍以內。或在 compose 和 largest_component 拒絕外框短邊太小（例如 < 16 px）或長寬比太極端的遮罩，丟 ValueError（就會變成「請重新產生角色動作」）。
- 把握: 可能（程式路徑確定；沒有看過真實的扁遮罩）

### C6 [低] [安全] dev_fake_backend 的 --storage 沒有防呆，指到正式資料夾會在 storage/app.db 上跑 migration、寫入假資料
- 位置: scripts/dev_fake_backend.py:213-225（demo_settings）、254
- 問題: `--storage` 會原樣變成 `paths.storage_dir` 和 `paths.database = <storage>/app.db`，接著 create_app 的 lifespan 會 `init_db`（migration）、啟動 worker，並寫入 DemoEngine 畫的圖。沒有檢查這個路徑是否等於設定裡真正的 storage_dir 或 storage-r2，也沒有檢查是否已經有非空的 app.db。docstring 說「不會碰到正式資料」，但只有在使用預設暫存資料夾時才成立。
- 會怎樣出錯: 開發者照 README 打 `--storage storage`（或 tab 補完錯），假後端就會升級並寫入正式資料庫和書本資料夾，而且 dev_owner=admin。
- 建議修法: 如果 `--storage` 解析後等於 `load_settings().paths.storage_dir`、正式 database 的上層資料夾，或含有不是由假後端建立的 app.db（例如加一個標記檔），就拒絕啟動。
- 把握: 確定（沒有防呆；造成傷害需要使用者打錯指令）

### C7 [低] [結構] 線稿／QA 快取版本號只放在一個模組，但演算法參數散在三個模組
- 位置: backend/app/services/lineart_render.py:18（RENDER_VERSION）；backend/app/services/qa.py:35、252（QA_VERSION；min_region_area 取自 LineartParams 的預設值）；backend/app/services/lineart_post.py:46-66（LineartParams 預設值）；backend/app/services/lineart_extract.py:35（xdog 預設值）
- 問題: lineart_key 和預覽 key 只包含 RENDER_VERSION、QA_VERSION 和頁面設定。實際的線稿還取決於 lineart_post 的 min_fragment_area、close_kernel、binarize 和 lineart_extract 的 k／p／eps／phi。QA 的「兩區面積 >= min_region_area」也取自 LineartParams 的預設值，不在 QAThresholds 裡。QA_VERSION 的註解只要求「QAThresholds 改了要 +1」。這兩個模組同時也是實驗的調參工具（experiments/run_matrix、metrics），有人改預設值時很容易漏掉另一個模組的版本號。
- 會怎樣出錯: 改了 lineart_post 或 lineart_extract 的預設值，已存的線稿、預覽和 qa_json 會繼續被當成最新的。不同頁面的線寬、清理程度會混用新舊演算法，QA 結果也跟實際線稿對不上。目前 git 紀錄裡這兩個模組在 RENDER_VERSION 出現後沒有改過演算法，所以還沒發生。
- 建議修法: render_base 改用自己固定的參數常數（不要依賴 LineartParams／METHODS 的預設值），或把那些參數一起放進 _digest。QA 的 min_region_area 搬進 QAThresholds。
- 把握: 確定（目前是潛在問題）

### C8 [低] [死碼] lineart_post 留著大量實驗用的開關，render_base 每次還白算一份沒人用的指標
- 位置: backend/app/services/lineart_post.py:32（A4_300DPI）、48-54（fit cover/stretch、interpolation、binarize fixed/adaptive）、100／261／274（keep_steps／steps）、199（save_1bit_png）、278（compute_metrics）、282（process_file）、288（params_dict）；backend/app/services/lineart_render.py:44（回傳 metrics）
- 問題: 產品只用 contain＋lanczos＋otsu，target 一律是 content_px。其他選項只服務 Phase 1 實驗。render_base 回傳的 metrics 在 page_lineart.py 兩處和 `_preview` 都被丟掉（`ink, _metrics`／`ink, _`），但 process() 每次都會跑 compute_metrics（連通元件、23 px 圓核 opening），在 A4 上實測約 0.08 秒。之後 QA 的 evaluate 還會在同一張圖上再算一次。
- 會怎樣出錯: 產品裡誰都沒呼叫。grep `process_file|params_dict|keep_steps|save_1bit_png` 的結果：process_file 只有 tests/test_lineart_post.py；params_dict 沒有任何地方用；keep_steps 只有本檔和 tests；save_1bit_png 只有 experiments/run_matrix.py 和 tests。
- 建議修法: 產品端加一個精簡的 `render_ink(gray, content_px, line_width)`，不算指標。實驗開關和 metrics 留給 experiments（或搬到 experiments/ 的薄包裝）。
- 把握: 確定

### C9 [低] [死碼] page_lineart.render_preview、qa.find_gaps 只剩測試在用
- 位置: backend/app/services/page_lineart.py:262；backend/app/services/qa.py:188
- 問題: 預覽端點已經改用 ensure_preview 加簽章網址轉址，render_preview（回傳 bytes）沒有產品呼叫端。evaluate 自己用 `_merge(_midpoint...)` 算 near_miss，沒有用 find_gaps。
- 會怎樣出錯: grep `render_preview` 只有 tests/test_page_lineart.py；`find_gaps\b` 只有 tests/test_qa.py。backend/app、frontend/src、scripts、experiments 都沒有。
- 建議修法: 刪掉這兩個函式，測試改成測 ensure_preview 和 evaluate 的 near_miss_count。
- 把握: 確定

### C10 [低] [死碼] 透明壓平與 1-bit 編解碼的 helper 各有 3–5 份，而且捨入方式不一致
- 位置: 透明壓平：backend/app/services/lineart_post.py:105（load_image）、116（to_gray 的 RGBA 分支，用 `astype` 截斷）；backend/app/services/lineart_extract.py:103（_rgb 的 RGBA 分支，用 `round`）；backend/app/services/compose.py:91（_flatten）；backend/app/storage.py:542（_flatten_on_white）。1-bit 編解碼：lineart_render.py:65（ink_to_png）、lineart_post.py:199（save_1bit_png）、export.py:16 的無標題分支；autofix.py:24（read_ink）與 api/export.py:41 各自解碼一次。compose.compose()（compose.py:119）也重寫了一遍 cutout()（:105）的尺寸檢查、外框和裁切。
- 問題: 同一件事有多份實作，細節不同（截斷或四捨五入；有沒有處理 "PA"／P＋transparency）。產品路徑上，to_gray 和 _rgb 的 RGBA 分支其實到不了（輸入已經先被 load_image 壓平），屬於死分支。
- 會怎樣出錯: 不會直接出錯；之後有人修其中一份（例如加上 16-bit 或 LA 的處理）時，其他幾份不會跟著改。這裡沒有 grep 到「沒人呼叫」的函式，主要是重複。
- 建議修法: 統一成一個 `flatten_on_white(Image)`（放在 storage 或一個小的 imageio 模組），加上 `ink_to_png`／`png_to_ink` 一對函式，其他模組都改用它們。compose() 改成直接呼叫 cutout()。
- 把握: 確定

### C11 [低] [結構] 名稱互相撞：兩個 compose_page、兩個 load_image、兩種 _disk 語意
- 位置: backend/app/services/page_compose.py:191（compose_page：產生合成圖 generation）與 backend/app/services/export.py:16（compose_page：把線稿排成列印頁）、backend/app/api/export.py:33（composed_page）；page_compose.py:149（load_image → PIL.Image，從 storage 讀）與 lineart_post.py:105（load_image → ndarray RGB，從路徑或檔案物件讀）；lineart_post.py:178、lineart_extract.py:143 的 `_disk(diameter)` 與 captions.py:132 的 `_disk(radius)`
- 問題: 同一個名稱在相鄰模組代表完全不同的東西。`_disk` 的參數一個是直徑、一個是半徑，複製程式碼時線寬會差一倍。另外 compose.py 自稱「純 Pillow／numpy」，卻 import app.models 來做 composite_matches（DB 紀錄比對）。
- 會怎樣出錯: 讀程式和搬程式碼時容易用錯（例如把 captions 的 `_disk(s)` 換成 lineart_post 的版本，外框粗細會差一倍）；compose.py 也沒辦法單獨在沒有 DB model 的環境（例如雲端 GPU worker）使用。
- 建議修法: export.compose_page 改名成 `layout_print_page`、api 的 composed_page 改名成 `print_page_image`；page_compose.load_image 改名成 `get_pil_image`；`_disk` 統一用直徑並改名成 `disk_kernel(diameter)`。composite_matches 搬到 page_compose。
- 把握: 確定

### C12 [低] [bug] sync_comfy_model_paths 說「其他區段原樣保留」，實際會刪掉整檔的註解與格式，第二次執行後 .bak 也不再是原檔
- 位置: scripts/sync_comfy_model_paths.py:62（yaml.safe_load）、81（safe_dump 重寫整檔）、167-169（每次都覆寫 .bak）
- 問題: 讀進來再整檔用 safe_dump 輸出，ComfyUI 範例檔裡大量的註解、引號、多行字串格式都會不見。.bak 每次執行都從當下的檔案複製，所以第一次執行之後 .bak 已經是被重寫過的版本，原檔再也找不回來。
- 會怎樣出錯: 使用者在 extra_model_paths.yaml 手動寫的其他區段（例如被註解掉的 a111 設定）第一次同步就沒了；第二次同步之後 .bak 也救不回來。
- 建議修法: 只在 .bak 不存在時備份（或備份檔加時間戳記）。docstring 改成「其他區段的內容保留，但註解與格式會被重寫」，或改用 ruamel.yaml 保留註解。
- 把握: 確定

### C13 [低] [bug] scripts/preflight.py 的 --workflow 打錯名稱時，靜靜地檢查 0 個 workflow 並以「有項目未通過」結束
- 位置: scripts/preflight.py:33；backend/app/services/preflight.py:216（`bool(report.workflows)`）
- 問題: 過濾之後的 registry 是空的，run_preflight 回報 ok=False，但 format_report 沒有任何 workflow 可以列出，只印出 ComfyUI 連線狀態和「結果：有項目未通過」。
- 會怎樣出錯: `--workflow qwen_backgroud`（拼錯）會讓人以為 ComfyUI 有問題，卻看不出是哪一項。
- 建議修法: 過濾前先檢查 `set(args.workflow) - registry.entries.keys()`，有未知名稱就印出可用的名稱並 return 2。
- 把握: 確定

### C14 [低] [安全] 修線與 QA 的 CPU 成本隨筆畫數、缺口數線性放大，而且每次都配置整頁陣列
- 位置: backend/app/services/lineart_render.py:47-62（每一筆都配置一張 h×w 的 uint8 遮罩，再 astype(bool)）；backend/app/services/autofix.py:54-58（survives() 對每座橋都 `apply_strokes(np.zeros_like(pre), [stroke])`）；backend/app/services/qa.py:158-185（Python 逐端點 BFS）
- 問題: 實測 A4 印刷廠（2551×3579）：500 筆畫的 apply_strokes 約 1.6 秒；有 3,421 個端點的 evaluate 約 2.6 秒。autofix 有 user 層時，每輪每座橋都要配置兩張整頁陣列，最多 3 輪，每輪後還要再跑一次 evaluate。這些都在同步 `def` 端點（threadpool）或 worker 裡執行。
- 會怎樣出錯: 合法輸入（MAX_STROKES=500×兩層、紋理多的 Qwen 背景產生數千個端點）就能讓單一請求花上十幾秒的 CPU。多個分頁或分享連結的檢視同時觸發時，threadpool 會被占滿。這不是崩潰，但在雲端小機器上會很明顯。
- 建議修法: apply_strokes 只在每筆的外框範圍（bounding box）內畫，或把所有同 op 的連續筆畫畫在同一張遮罩上；survives() 改成只取筆畫外框內的 erased 子陣列來比對。QA 可以先限制端點數，或用 cv2 的距離轉換取代逐點 BFS。
- 把握: 確定（耗時是實測的；對使用體驗的影響是推估）

---

統計：中 2（bug 1、死碼 1）；低 12（bug 4、安全 3、結構 2、死碼 3）。合計：bug 5、死碼 4、結構 2、安全 3。

已檢查、沒發現問題的部分（避免重複審查）：
- layout：五種紙張×兩種模式的 page_px、offset、trim／safe box 都正確。印刷廠右邊和下邊的出血比左上多 1 px（35／36），可以忽略。
- 標題：12 字中文、28 字英文、含下伸部（descender）的字母，在所有紙張和模式下都落在文字帶和安全區內，中英兩行不重疊。
- PDF：頁面尺寸＝page_px／300，本機 Pillow 13 有 libtiff，1-bit 頁面以 CCITT 編碼。
- autofix：bridge_stroke 和前端 strokes.ts 的 (w-1) 換算一致。
- 上傳：背景與主角都已裁切或縮放到生成尺寸，所以使用者上傳不會讓線稿流程配置巨大影像。
