import type { QueryClient } from "@tanstack/react-query";
import { forgetEditsQueues } from "./editsQueue";
import { forgetSentFields } from "./sentFields";
import { placementSaverFor } from "./placementSaver";
import { sketchSaverFor } from "./sketchSaver";

/**
 * 前端以頁 id（修線再加選圖 id）為 key 的模組層級狀態：草圖存檔、送出過的場景描述／構圖強度、修線佇列。
 * 後端的 id 不會沿用（AUTOINCREMENT），但舊資料庫或其他後端仍可能沿用，所以兩端都防：
 * 刪除時清一次，id 又出現在新頁上時再整個清掉。
 */

/** 頁面刪除成功。草圖記成已刪除：之後回來的 404 與卸載 flush 都不報錯、不送出。 */
export function forgetDeletedPage(qc: QueryClient, pageId: number) {
  sketchSaverFor(qc).endDelete(pageId, true);
  placementSaverFor(qc).markGone(pageId);
  forgetSentFields(qc, pageId);
  forgetEditsQueues(qc, pageId);
}

/** 這個 id 現在是一個新頁（剛建立，或已刪除的 id 又出現在頁面清單）：當成全新的頁，連已刪除標記都清掉。 */
export function forgetPageId(qc: QueryClient, pageId: number) {
  sketchSaverFor(qc).reset(pageId);
  placementSaverFor(qc).reset(pageId);
  forgetSentFields(qc, pageId);
  forgetEditsQueues(qc, pageId);
}
