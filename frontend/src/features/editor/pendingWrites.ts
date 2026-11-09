import type { QueryClient } from "@tanstack/react-query";
import type { Page } from "@/api/client";
import { qk } from "@/api/hooks";
import { allEditsQueues } from "./editsQueue";
import { placementSaverFor } from "./placementSaver";
import { sketchSaverFor } from "./sketchSaver";

/**
 * 跨元件的「還沒存到伺服器」狀態：草圖存檔（送出中、失敗、畫布 debounce 中）、位置存檔與修線佇列（送出中、失敗）。
 * 都存在模組層級（每個 QueryClient 一份），元件卸載後仍然有效。
 */
export function hasPendingWrites(qc: QueryClient): boolean {
  if (sketchSaverFor(qc).hasPending()) return true;
  if (placementSaverFor(qc).hasPending()) return true;
  return allEditsQueues(qc).some((e) => e.busy() || e.failed);
}

/** beforeunload：還有沒存好的修改就請瀏覽器確認離開（關分頁、重新整理）。 */
export function onBeforeUnload(qc: QueryClient, e: BeforeUnloadEvent): void {
  if (!hasPendingWrites(qc)) return;
  e.preventDefault();
  // 舊版 Chrome／Safari 要設 returnValue 才會詢問
  e.returnValue = "";
}

/**
 * 等這本書所有的寫入結束（先 flush 掛載中的草圖畫布）：匯出前用，確保 PDF 用的是畫面上的內容。
 * 全部結束後，任何一個最後寫入失敗 → reject 第一個錯誤。
 * 修線只看各頁「目前選定圖」的佇列（PDF 只用選定圖）：換選圖後保留的舊佇列（失敗的留著供選回時取回筆畫）不擋匯出。
 * 還沒有頁面資料（或頁面不在快取裡）時保守地等。
 */
export async function whenBookWritesIdle(qc: QueryClient, bookId: number): Promise<void> {
  const pages = qc.getQueryData<Page[]>(qk.pages(bookId));
  const selected = pages ? new Map(pages.map((p) => [p.id, p.selected_generation_id])) : null;
  const results = await Promise.allSettled([
    sketchSaverFor(qc).whenBookIdle(bookId),
    ...allEditsQueues(qc)
      .filter(
        (e) => e.bookId === bookId && (!selected?.has(e.pageId) || selected.get(e.pageId) === e.genId),
      )
      .map((e) => e.flush()),
  ]);
  const bad = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
  if (bad) throw bad.reason;
}
