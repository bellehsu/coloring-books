import type { QueryClient } from "@tanstack/react-query";
import { forgetEditsQueues } from "./editsQueue";
import { placementSaverFor } from "./placementSaver";

/**
 * 前端以頁 id（修線再加選圖 id）為 key 的模組層級狀態：位置存檔、修線佇列。
 * 後端的 id 不會沿用（AUTOINCREMENT），但舊資料庫或其他後端仍可能沿用，所以兩端都防：
 * 刪除時清一次，id 又出現在新頁上時再整個清掉。
 */

/** 頁面刪除成功。位置記成已刪除（placementSaver.isGone）：之後回來的 404 不報錯、之後的存檔不送出。 */
export function forgetDeletedPage(qc: QueryClient, pageId: number) {
  placementSaverFor(qc).markGone(pageId);
  forgetEditsQueues(qc, pageId);
}

/** 這個 id 現在是一個新頁（剛建立，或已刪除的 id 又出現在頁面清單）：當成全新的頁，連已刪除標記都清掉。 */
export function forgetPageId(qc: QueryClient, pageId: number) {
  placementSaverFor(qc).reset(pageId);
  forgetEditsQueues(qc, pageId);
}
