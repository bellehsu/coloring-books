import type { QueryClient } from "@tanstack/react-query";
import type { Page } from "@/api/client";
import { qk } from "@/api/hooks";
import { allEditsQueues } from "./editsQueue";
import { placementSaverFor } from "./placementSaver";

/**
 * 跨元件的「還沒存到伺服器」狀態：位置存檔與修線佇列（送出中、失敗）。
 * 都存在模組層級（每個 QueryClient 一份），元件卸載後仍然有效。
 */
export function hasPendingWrites(qc: QueryClient): boolean {
  if (placementSaverFor(qc).hasPending()) return true;
  return allEditsQueues(qc).some((e) => e.busy() || e.failed);
}

/**
 * beforeunload：還有沒存好的修改就請瀏覽器確認離開（關分頁、重新整理）。
 * pending：呼叫端自己的、不在這裡追蹤的寫入（C 的位置與文字存檔）還沒結束。
 */
export function onBeforeUnload(qc: QueryClient, e: BeforeUnloadEvent, pending = false): void {
  if (!pending && !hasPendingWrites(qc)) return;
  e.preventDefault();
  // 舊版 Chrome／Safari 要設 returnValue 才會詢問
  e.returnValue = "";
}

export interface WriteFailure {
  pageId: number;
  /** edits：修線；placement：角色位置。 */
  kind: "edits" | "placement";
}

/** 匯出前有寫入失敗：message 是第一個失敗的原因，failures 列出是哪幾頁的什麼寫入（提示與「重送」用）。 */
export class BookWritesError extends Error {
  constructor(
    message: string,
    readonly failures: WriteFailure[],
  ) {
    super(message);
    this.name = "BookWritesError";
  }
}

/** 這本書「目前選定圖」的修線佇列（PDF 只用選定圖）；還沒有頁面資料（或頁面不在快取裡）時保守地全算。 */
function selectedEdits(qc: QueryClient, bookId: number) {
  const pages = qc.getQueryData<Page[]>(qk.pages(bookId));
  const selected = pages ? new Map(pages.map((p) => [p.id, p.selected_generation_id])) : null;
  return allEditsQueues(qc).filter(
    (e) => e.bookId === bookId && (!selected?.has(e.pageId) || selected.get(e.pageId) === e.genId),
  );
}

/**
 * 等這本書所有的寫入結束：各頁的修線佇列與角色位置存檔（匯出前用，確保 PDF 用的是畫面上的內容）。
 * 全部結束後，任何一個最後寫入失敗 → reject BookWritesError（第一個錯誤的訊息＋失敗清單）。
 * 修線只看各頁「目前選定圖」的佇列：換選圖後保留的舊佇列（失敗的留著供選回時取回筆畫）不擋匯出。
 */
export async function whenBookWritesIdle(qc: QueryClient, bookId: number): Promise<void> {
  const saver = placementSaverFor(qc);
  const jobs: { failure: WriteFailure; run: Promise<unknown> }[] = [
    ...selectedEdits(qc, bookId).map((e) => ({ failure: { pageId: e.pageId, kind: "edits" } as const, run: e.flush() })),
    ...(qc.getQueryData<Page[]>(qk.pages(bookId)) ?? []).map((p) => ({
      failure: { pageId: p.id, kind: "placement" } as const,
      run: saver.whenIdle(p.id),
    })),
  ];
  const results = await Promise.allSettled(jobs.map((j) => j.run));
  const bad = results.flatMap((r, i) => (r.status === "rejected" ? [{ reason: r.reason, failure: jobs[i].failure }] : []));
  if (bad.length === 0) return;
  const first = bad[0].reason;
  throw new BookWritesError(first instanceof Error ? first.message : "儲存失敗", bad.map((b) => b.failure));
}

/** 「重送」：把失敗的佇列最新的內容再送一次（修線 push latest、位置 retry），然後等它們寫完；仍失敗 → reject。 */
export function retryFailedWrites(qc: QueryClient, bookId: number, failures: WriteFailure[]): Promise<void> {
  const saver = placementSaverFor(qc);
  for (const f of failures) {
    if (f.kind === "placement") saver.retry(f.pageId);
    else
      for (const e of selectedEdits(qc, bookId))
        if (e.pageId === f.pageId && e.failed && e.latest) e.push(e.latest);
  }
  return whenBookWritesIdle(qc, bookId);
}
