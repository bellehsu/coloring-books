# 清掉框外雜物（背景清理）：設計

日期：2026-11-24　狀態：草稿（已過 builder check），等使用者確認
接在 `2026-11-24-portrait-frame-fit-design.md`（已合併 main af54845）之後。新分支 `background-cleanup-2026-11-24`。

## 1. 為什麼

華麗相框的背景常在框外多畫東西（氣球、圓圈、兔子、小愛心），四葉草每張外面都多包一圈方框。改文字寫法試了很多輪，
只有約一半機會乾淨；寫出「沒有 XX」反而會畫出 XX。使用者問「qwen 可以讓外框外面不要再有雜物嗎？」並要四葉草外面不要方框。

試做（`experiments/results/frames_pack_acceptance_20261124/qwen_edit_clean.png`、`qwen_edit_clover.png`）：
把畫好的背景交給 Qwen-Image-Edit-2511「編輯」一次（同一個模型、同樣的 GGUF＋Lightning 4 步，約 20 秒）：
- 通用指令（§5.3 GENERIC）：圓框旁的氣球、菱形旁的圓圈、橢圓旁的兔子 **3/3 清乾淨**，框本身不變。
- 四葉草外圍方框：通用指令 0/4；專用指令（§5.3 CLOVER）**2/2 移掉**；另一種「擦掉靠近邊緣的線」寫法 0/2。
- 程式擦掉「不和框相連的墨跡」只有 1/12 有效（雜物多半碰到框），不採用。

## 2. 使用者的決定（2026-11-24）

| 題目 | 決定 |
|---|---|
| 做法 | 背景畫好後讓 Qwen 再編輯一次，清掉框外雜物 |
| 開啟方式 | **相框包自動＋按鈕**：華麗相框包每頁自動清；背景卡片加「清掉框外雜物」按鈕，其他書需要時手動按 |
| 草圖 | https://claude.ai/artifact/UTGEvemTWYbErYGe7sGuVh 第二個畫面（進階編輯背景卡片、一鍵處理頁背景列、相框包自動流程） |
| 合併 | 肖像模式分支先合併（已完成），清理功能另開分支 |
| 生成次數（builder check 後） | **算**：自動清理也扣每日生成次數，做書前先預估（「這次需要 36 次」） |
| 按鈕範圍（builder check 後） | **只在肖像模式的頁**（頁面位置有 `frame`）可以按；其他頁停用並說明「有框的背景（肖像模式）才能清」 |

## 3. 目標、非目標、反目標

**目標**
- 一鍵做相框書時，每頁背景畫好後自動清一次，再合成肖像；四葉草頁連外圍方框一起拿掉。
- 肖像模式的頁（不分哪本書）都能按「清掉框外雜物」清一次；清好的是一張新背景，舊的留著可以點回去（進階編輯）或「↶ 復原」（一鍵處理頁）。
- 同一套生成紀錄：清理是一筆 generation，保存 workflow、版本、參數、seed、提示詞、耗時、錯誤（CLAUDE.md 規定）。

**非目標**
- 讓使用者自己寫清理指令；清理指令的介面。
- 其他主題包自動清；清理角色動作或合成圖。
- 程式（非 AI）擦雜物。

**反目標**
- 沒有開自動清理的頁面、沒有按按鈕的頁面，流程與今天完全相同（不多跑 GPU、不多一步）。
- 長方形類的框（長方形、蝴蝶結、圓角方形、皇冠）不可以被當成雜物擦掉：只有四葉草頁用「刪掉方框」的指令。
- 清理失敗不能讓頁面卡住：退回原本那張背景，照常合成。
- 不新增模型下載；不新增佇列服務。

## 4. 做法的選擇

1. **清理是一種背景生成（採用）**：kind 仍是 BACKGROUND，多記「清理自哪一張背景」；用新 workflow `qwen_edit_cleanup`
   （與角色動作同一組 Qwen Edit 節點，輸入圖換成那張背景，不去背）。好處：縮圖、選用、復原、配額、生成紀錄、
   合成與補線全部沿用背景的路徑。
2. 新的 generation kind：要改所有依 kind 分流的地方（縮圖、選用、合成、狀態），不採用。
3. 清理後覆蓋原背景檔：失去「點回去」與生成紀錄，違反 CLAUDE.md，不採用。

## 5. 設計

### 5.1 資料

- `Page` 新欄位（migration 0016）：
  - `auto_cleanup: bool = False`：背景（AI 畫的）產生好後自動清一次。
  - `cleanup_prompt: str | None`：這頁專用的清理指令（英文，≤ 600 字元）；null＝用通用指令 GENERIC。
- `PATCH /api/pages/{id}` 接受這兩個欄位；`PageOut` 回傳它們。
- 清理的 generation：`kind=BACKGROUND`、`user_text`＝空字串（不是場景描述）、`prompt`＝實際用的清理指令、
  `width/height`＝來源背景的尺寸、`request_json` 的參數記錄 `cleanup_of: <來源 generation id>`；
  另加 nullable 欄位 `source_generation_id`（同一個 migration）讓查詢與前端標示不必解析 JSON。

### 5.2 Workflow `comfy_workflows/qwen_edit_cleanup/`

- `workflow_api.json`：以 `qwen_edit_pose_cutout` 為底，拿掉 BiRefNet 去背的四個節點（LoadBackgroundRemovalModel、
  RemoveBackground、MaskToImage、MASK 的 SaveImage）；輸入圖節點 title 改 `SOURCE_IMAGE`；輸出只有 `OUTPUT`。
- `manifest.yaml`：參數 `prompt`、`source_image`（image）、`seed`、`width`、`height`，其餘固定值與角色動作相同；
  models 與 custom_nodes 同 `qwen_edit_pose_cutout`。節點類別與欄位實作時用執行中 ComfyUI 的 `/object_info` 驗證（CLAUDE.md）。
- `ComfyEngine`：`GenerationRequest` 加 `source_image: bytes | None`；kind 是 BACKGROUND 且有 `source_image` 時用
  `qwen_edit_cleanup` 並上傳那張圖；否則照舊。假引擎（tests/e2e 用）回傳來源圖本身（或加一點可辨識的變化）。

### 5.3 清理指令（後端常數，英文，原樣送 Qwen）

- GENERIC：「Remove every object outside the ornate frame (balloons, circles, clouds, hearts, animals, ground lines).
  Keep the ornate frame exactly the same: same lines, same shape, same position and size, and keep the inside of the
  frame empty white. Everything outside the frame is plain pure white. Black and white line art, clean black outlines.」
- CLOVER（放在主題包資料裡，§5.6）：「Delete the rectangular decorative border that runs along the four edges of the
  picture, including its corner ornaments. Only the four-leaf clover shaped frame in the middle remains, unchanged.
  The rest of the page is plain pure white. Black and white line art.」

### 5.4 API 與流程

- `POST /api/pages/{page_id}/backgrounds/cleanup`（可帶 `?oneclick=true`）：用這頁**目前選用的背景**排一筆清理
  （指令＝`cleanup_prompt` 或 GENERIC）；沒有選用的背景 → 409（沿用現有缺背景訊息）；頁面位置沒有 `frame`（不是肖像模式）→ 409
  「有框的背景（肖像模式）才能清」。計入每日生成次數（與「換一張」相同）。
  一般：好了自動選用（與新背景相同的 take 規則）；oneclick：與「AI 再畫一張」相同——先存復原快照、好了一定換上、重新合成。
  上傳的背景也可以按（使用者自己決定）。
- **自動清理**：一張**不是清理產生的**、AI 畫的背景成功時，若頁面 `auto_cleanup` 為 true：
  - 不選用這張，改排一筆清理它的 generation（同一個交易），清理成功時用與它相同的選用規則換上（一鍵流程接著合成）；
  - 清理失敗：選用原本那張背景並照常排頁面流程（頁面不卡住），清理錯誤記在那筆 generation 上；
  - 清理產生的背景成功時不再觸發清理（不會連鎖）。
  - 「換一張／AI 再畫一張」產生的新背景在 `auto_cleanup` 頁上也會自動清（一致）。
- 頁面狀態：清理排隊或執行中時，這頁算「製作中」（沿用背景生成中的判斷）。
- **生成次數**（使用者決定：算，事先預估）：扣的是這頁所屬書的擁有者（與來源背景相同的 usage owner）。
  - 整本做書（produce／produce_plan）：`auto_cleanup` 頁的背景算 2 次（背景＋清理），計畫的總數、`fits`、預估時間與
    「今天只剩 X 次，這次需要 N 次」都照這個算；次數不夠時照今天的規則只做做得完的頁。
  - 在 `auto_cleanup` 頁按「換一張／AI 再畫一張」：一次預留 2 次；不夠時照今天的 429 訊息。
  - 自動清理觸發時若次數已用完（例如預留後別處又用掉）：不清，直接選用原本那張背景照常合成（不超過上限）。
- **改場景文字就關掉自動清理**（我的判斷）：`PATCH` 讓 `scene_prompt` 變了時，同時把 `auto_cleanup` 設 false、`cleanup_prompt`
  清成 null（主題包的清理指令是配合原本的場景寫的）；按鈕仍可用（用 GENERIC）。
- **按鈕的清理失敗**（我的判斷）：不設 hard attention、不影響「再試一次」；原本的背景維持選用，失敗那筆照現有方式
  顯示在縮圖／錯誤提示。自動清理失敗：選用原本那張背景照常合成，也不設 attention。

### 5.5 前端

照草圖：
- 進階編輯 ①構圖右欄背景卡片：「換一張」「上傳背景圖」旁加「清掉框外雜物」；沒有選用的背景、這頁背景正在產生／清理、
  或這頁不是肖像模式時停用（不是肖像模式時旁邊一行說明「有框的背景（肖像模式）才能清」）。
- 一鍵處理頁背景列：「AI 再畫一張」「↑ 上傳圖片」旁加同一顆（走 `?oneclick=true`）。
- 縮圖：清理產生的背景在縮圖上加一個小標「清過」（`source_generation_id` 不是 null），讓使用者分得出來（我的判斷）。
- 主題包：`ThemePack` 加選填 `cleanups?: { auto: boolean; prompt?: string }[]`（依頁序）；套用時跟 `placements` 一樣
  存到各頁（`PATCH` `auto_cleanup`、`cleanup_prompt`），自己改過貼上的文字就不套用（同 lineDetail、placements）。

### 5.6 華麗相框包

- 12 頁都 `auto: true`；第 4 頁四葉草 `prompt: CLOVER`（§5.3 原文），其他頁不帶 prompt（用 GENERIC）。
- 場景文字不變。

## 6. 驗收

- 單元測試：migration；PATCH／PageOut 新欄位與驗證（長度、型別）；清理 job 的 prompt／尺寸／source；engine 選 workflow
  與上傳來源圖（mock ComfyUI）；manifest 載入與 preflight 列出新 workflow 需要的模型與節點；自動清理觸發、不連鎖、
  失敗退回原圖；按鈕端點（一般／oneclick、缺背景 409、非肖像 409、配額）；produce 計畫把自動清理算進次數；
  改場景文字關掉自動清理；自動清理時次數用完 → 不清、照常合成；按鈕清理失敗不設 attention；沒開自動清理的頁面流程不變。
- 前端：兩處按鈕（停用條件、送出、oneclick）、「清過」小標、主題包套用 cleanups。
- GPU 驗收（先通知 roboskin-38；約 15 分鐘）：用雪花小女孩做一本相框書，縮圖總表；四葉草頁沒有外框方框、
  其他頁框外乾淨、長方形類框完整；再在瀏覽器按一次兩處按鈕（截圖給使用者）。

## 7. 留給實作者

假引擎回傳的內容、`source_generation_id` 的外鍵與刪除規則（照現有 generation 欄位的做法）、清理 generation 的
進度顯示文字（沿用背景）、e2e 是否需要擴充假後端。
