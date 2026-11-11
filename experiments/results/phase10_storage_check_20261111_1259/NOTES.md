# Phase 10 真 R2 驗收（2026-11-11）

## 設定
- 分支 `phase10-storage`（Task 8 修正 8dde123 之後）。真的後端 app（`create_app`）＋真的 R2 bucket `coloring-books-dev`；
  生成引擎用 `scripts/dev_fake_backend.py` 的 `DemoEngine`（使用者決定：不用 GPU，生成品質 Phase 9 已驗過）。
- 後端 8001（`storage.backend=r2`、`auth.dev_owner` 關閉、全新 DB `storage-r2/app.db`、不跑 preflight），前端 `next dev` 3001。
  R2 憑證只從 `scripts/dev.env` 以環境變數帶入，沒有讀出或印出；紀錄中的 R2 主機以 `<account>` 遮蔽。
- 邀請：`scripts/invites.py create --admin`（admin）與 `create --cap 2`（使用者 B）。
- 腳本 `accept_run.mjs`；截圖、PDF、`results.json`、`log.txt` 在 `processed/`（不進版控）。

## 結果
| 檢查 | 結果 |
|---|---|
| 第一次啟動（空 DB＋空 bucket） | 自動建立 `books/_instance.json` 與 DB 的 `storage_instance_id`，兩邊一致 |
| 沒 cookie 呼叫 `/api/books` | 401 |
| 邀請連結 `/i/{token}` | 303 → `/`，cookie `cb_session` httpOnly、SameSite=Lax |
| 角色 2 張 → 故事貼 3 頁（第 3 頁上傳背景 201）→ 產生全部素材 | 6.4 s（假引擎），故事頁 0 張破圖 |
| 3 頁：開頁自動合成 → 補線（1/1/2 缺口 → 0）→ 對 → 完成 | 3 頁都完成，每頁 7.8–9.8 s，0 張破圖 |
| 瀏覽器載入的 R2 圖片 | 28 次全部 `200 image/png`（`<img>` 經 302 直連 R2，不需 CORS） |
| `GET /api/generations/{id}/image` | 302、`Cache-Control: private, max-age=60`、Location 是 `<account>.r2.cloudflarestorage.com` 的 presigned URL（`X-Amz-Expires=120`）；立即取用 200 `image/png` 73 KB |
| `GET /api/pages/{id}/lineart` | 302 → R2 |
| presigned 過期（等 135 s） | 403，訊息含 expired |
| 匯出 PDF | `%PDF`，21 KB |
| 使用者 B（另一個瀏覽器 context） | 書單 0 本；A 的書 404、A 的圖 404、A 的 PDF 404 |
| B 每日上限 2 | 角色候選 202、202、第 3 次 429「今天的生成次數已用完，明天再來」 |
| `storage_gc.py --dry-run` | exit 0；過期捨棄 0、孤兒 0、略過 0（instance 標記一致） |
| `invites.py delete-data`（B、admin `--force-admin`） | 兩個都 exit 0；bucket 裡 `books/1/`、`books/2/` 全部刪除，只剩標記 |

## 收尾
- 驗收後把 bucket 的 `books/_instance.json` 刪掉、移除本機 `storage-r2/`：`coloring-books-dev` 回到空的，
  合併後本機第一次啟動會照「空 DB＋空 bucket」規則重新建立標記。
- 沒有用 GPU；沒有碰 3000／8000／8188 的服務與正式 `storage/`。

## 觀察
- `cache/previews/` 在 R2 上 0 個：這次流程沒有預覽物件寫進 bucket（原因留給最終審查確認）。
- 假引擎的圖很小，耗時不代表真實 GPU 生成；真實耗時見 Phase 9 驗收。
