# 2026-11-13 審查留下的四個 bug — 根因（2026-11-18 調查）

來源：polish 審查（2026-11-13）留下的待修項目。2026-11-18 以唯讀調查加探針測試確認。

## 共同根因（bug 1、2）
C1（04cc654）在改紙張／列印方式時，伺服器把已存的筆畫座標換算到新版面，但筆畫本身**沒有記錄座標屬於哪個版面**。
所以任何「在版面改之前算好、改之後才存」的寫入都會把舊座標存回去：進階編輯的 PUT、流程的自動補線層、自動補線 API、
另一個分頁開著的 C 頁。修法：伺服器在寫入筆畫層時檢查版面沒變（條件式 UPDATE），用戶端改版面前先把待存的寫入送完、
改版面後重新載入筆畫。

## Bug 1（重要，已確認）進階編輯開著時改紙張，下一筆把舊座標存回去
- `LineFixStage.tsx:47` 的 key 是 `${pageId}-${genId}`，版面改了不會重新載入；`LineFixEditor` 用 `useState` 留著舊筆畫。
- `BookSettingsDialog.tsx:54-66` 送 PATCH 前沒等待待存的筆畫寫入；`useUpdateBook.onSuccess`（`api/hooks.ts:285-298`）沒 invalidate `["edits"]`。
- `put_edits`（`api/lineart.py:193-224`）只檢查選用的生成圖，舊座標的整份筆畫 PUT 回來 200 成功。探針測試已重現。
- `editsQueue` 的 `entry.latest` 跨版面保留，會被 `retrySave`、`retryFailedWrites` 重送。

## Bug 2（小，已確認：非 PASSED 頁會一直錯）流程跑到一半改版面，存下舊版面的自動補線層
- `pipeline.py:415-420` 用當下版面算 auto 層 → PATCH 換算已存的層 → `commit_layer`（`page_lineart.py:118`）只檢查選用圖，把舊層蓋回去 → 頁面 PASSED。
- `_pipeline_pages`（`books.py:164-171`）只重排 PASSED 頁；第一次跑的頁面就一直錯。同樣的競態在自動補線 API（`api/lineart.py:227-277`）。

## Bug 3（部分成立）進階編輯的「看過了 ✓」在重新合成時按下去 409
- 按鈕在 `FinishPanel.tsx:304`，條件 `review`（`:65-66`）沒看 `recompose_pending`。正常情況下補線步驟在重新合成時會被擋住，
  只有頁面快取過期（另一個分頁改過）才看得到。409 是 `STILL_RECOMPOSING`（`api/history.py:106`），錯誤路徑沒重抓頁面。

## Bug 4（已確認，舊問題）Filmstrip「整本進度」把有問題的 PASSED 頁也算完成
- `Filmstrip.tsx:60` 只看 `status === "passed"`；同檔的狀態標籤與書本總覽（`services/page_state.py:50-59`）都不算 hard attention 或正在重新合成的頁。
