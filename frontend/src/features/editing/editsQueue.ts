import type { QueryClient } from "@tanstack/react-query";
import { ApiError, api, type QA } from "@/api/client";
import { qk } from "@/api/hooks";
import type { Stroke } from "@/lib/strokes";
import { createWriteQueue, type WriteQueue } from "@/lib/writeQueue";

/**
 * 每個（QueryClient, 頁, 選圖）只有一個修線寫入佇列，存在模組層級，比元件活得久：
 * 換模式／換頁再回來（元件重新掛載）時沿用同一個佇列，舊請求還在路上時新的不會並行送出，
 * 「同一頁的修改圖層依序寫入」在重新掛載後仍成立。
 */
export interface EditsEntry {
  readonly bookId: number;
  readonly pageId: number;
  readonly genId: number;
  /** contentPx：畫這些筆畫時的 book.layout.content_px（伺服器用來擋舊版面的座標）；省略＝沿用上一次的。 */
  push(strokes: Stroke[], contentPx?: readonly [number, number]): void;
  flush(): Promise<QA | undefined>;
  busy(): boolean;
  /** 訂閱忙碌／失敗狀態變化（useSyncExternalStore 用）。 */
  subscribe(fn: () => void): () => void;
  /** 最後 push 的完整筆畫（尚未確認也算）；這個 session 沒 push 過為 null。 */
  readonly latest: Stroke[] | null;
  /** latest 是在哪個版面（content_px）畫的。 */
  readonly latestPx: readonly [number, number] | null;
  /** 最新一次寫入失敗（伺服器與畫面不一致），下一次 push 清除。 */
  readonly failed: boolean;
  /**
   * 重新掛載時要用 latest 而不是伺服器的 edits：佇列忙碌（伺服器還沒追上）或最後一次寫入失敗。
   * 閒置且成功時伺服器就是真相，從 useEdits seed。
   */
  shouldSeedFromLocal(): boolean;
  /** 設定寫入失敗的回報（目前掛載的元件換成自己的 toast）。 */
  setErrorHandler(fn: (error: unknown) => void): void;
  /**
   * 目前掛著的修線畫布登記的自動補線，回傳補了幾條（失敗已提示過 → null）；沒有畫布為 null。
   * 面板的「自動補線」有畫布時要走這裡：先讓畫面上的筆畫寫完、補線期間延後寫入；補上的線存在 auto 圖層
   * （不併進使用者筆畫）。
   */
  readonly autofix: AutofixRunner | null;
  setAutofix(fn: AutofixRunner | null): void;
}

export type AutofixRunner = () => Promise<number | null>;

/** oneclick：C 處理一頁的筆畫（PUT 帶 ?oneclick=true：先存復原快照、流程做的頁不退回草稿）。
 * 和舊編輯器的佇列分開（key 不同），兩邊不會共用同一個佇列。 */
export interface EditsQueueOptions {
  oneclick?: boolean;
}

/** 伺服器的「筆畫是舊版面座標」（PUT 帶的 content_px 跟書本目前版面不同，見後端 page_lineart.STALE_LAYOUT）。 */
export const STALE_LAYOUT = "紙張或列印方式剛改過，請重新整理這一頁再畫";
const isStaleLayout = (e: unknown) => e instanceof ApiError && e.status === 409 && e.detail === STALE_LAYOUT;

const registry = new WeakMap<QueryClient, Map<string, EditsEntry>>();
const keyOf = (pageId: number, genId: number, opts?: EditsQueueOptions) =>
  `${pageId}:${genId}${opts?.oneclick ? ":c" : ""}`;

/** 這個 QueryClient 目前所有的修線佇列（離開頁面前檢查、匯出前等待用）。 */
export function allEditsQueues(qc: QueryClient): EditsEntry[] {
  return [...(registry.get(qc)?.values() ?? [])];
}

/** 只查詢、不建立。 */
export function peekEditsQueue(
  qc: QueryClient,
  pageId: number,
  genId: number,
  opts?: EditsQueueOptions,
): EditsEntry | undefined {
  return registry.get(qc)?.get(keyOf(pageId, genId, opts));
}

/** 同一頁同一張圖的兩個佇列（進階編輯的、C 的 oneclick）有沒有一個還在送。 */
export function editsBusyFor(qc: QueryClient, pageId: number, genId: number): boolean {
  return [peekEditsQueue(qc, pageId, genId), peekEditsQueue(qc, pageId, genId, { oneclick: true })].some((e) => e?.busy());
}

/**
 * 等同一頁同一張圖的兩個佇列（進階編輯的、C 的 oneclick）都送完：後端 PUT 是整份覆蓋、最後提交者勝，
 * 一邊寫入前另一邊的請求不能還在路上。兩個都結束才回報；有一個最後寫入失敗就 reject 它的錯誤。
 */
export async function flushAllEditsFor(qc: QueryClient, pageId: number, genId: number): Promise<void> {
  const entries = [peekEditsQueue(qc, pageId, genId), peekEditsQueue(qc, pageId, genId, { oneclick: true })];
  const results = await Promise.allSettled(entries.map((e) => e?.flush()));
  const failed = results.find((r) => r.status === "rejected");
  if (failed) throw failed.reason;
}

/**
 * 移除這一頁「閒置、沒失敗、且不再是選定圖」的佇列（換選圖後不再需要）。
 * 忙碌的留著把請求送完；失敗的留著，重新選回那張圖時還能拿回沒存成功的筆畫。
 */
export function pruneEditsQueues(qc: QueryClient, pageId: number, selectedGenId: number | null) {
  const map = registry.get(qc);
  if (!map) return;
  for (const [k, e] of map) {
    if (e.pageId === pageId && e.genId !== selectedGenId && !e.busy() && !e.failed) map.delete(k);
  }
}

/** 頁面刪除後移除它所有的佇列（還在路上的請求照常結束）。 */
export function forgetEditsQueues(qc: QueryClient, pageId: number) {
  const map = registry.get(qc);
  if (!map) return;
  for (const [k, e] of map) if (e.pageId === pageId) map.delete(k);
}

export function editsQueueFor(
  qc: QueryClient,
  bookId: number,
  pageId: number,
  genId: number,
  opts?: EditsQueueOptions,
): EditsEntry {
  let map = registry.get(qc);
  if (!map) {
    map = new Map();
    registry.set(qc, map);
  }
  const key = keyOf(pageId, genId, opts);
  const putUrl = `/pages/${pageId}/lineart/edits${opts?.oneclick ? "?oneclick=true" : ""}`;
  const found = map.get(key);
  if (found) return found;

  /**
   * 寫入 edits 快取前先取消進行中的 GET：它可能在寫入前發出、晚於寫入回來，帶著舊筆畫蓋掉快取。
   * 取消會同步把快取還原成 GET 前的狀態，接著才寫入新資料，所以不會被蓋掉。
   */
  const setEdits = (strokes: Stroke[]) => {
    void qc.cancelQueries({ queryKey: qk.edits(pageId) });
    qc.setQueryData(qk.edits(pageId), { generation_id: genId, strokes });
  };

  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((fn) => fn());
  let latest: Stroke[] | null = null;
  let latestPx: readonly [number, number] | null = null;
  let failed = false;
  let reportError: (error: unknown) => void = () => {};
  let autofix: AutofixRunner | null = null;

  const queue: WriteQueue<QA> = createWriteQueue<QA>(
    (strokes) =>
      api.put<QA>(putUrl, { generation_id: genId, strokes, ...(latestPx ? { content_px: [...latestPx] } : {}) }),
    {
      onSuccess: (qa, strokes) => {
        failed = false;
        setEdits(strokes);
        qc.setQueryData(qk.qa(pageId), qa);
        // lineart_url／needs_fix／status（修線會把通過退回草稿）與整本統計
        void qc.invalidateQueries({ queryKey: qk.pages(bookId) });
        void qc.invalidateQueries({ queryKey: qk.book(bookId) });
        void qc.invalidateQueries({ queryKey: qk.books() });
        void qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) });
        if (opts?.oneclick) void qc.invalidateQueries({ queryKey: qk.overview(bookId) });
      },
      onError: (err) => {
        if (isStaleLayout(err)) {
          // 書本版面已經改了（伺服器已把存好的筆畫換算過）：丟掉舊座標的筆畫，重新載入版面與換算後的筆畫
          latest = null;
          latestPx = null;
          failed = false;
          void qc.invalidateQueries({ queryKey: qk.edits(pageId) });
          void qc.invalidateQueries({ queryKey: qk.book(bookId) });
          void qc.invalidateQueries({ queryKey: qk.pages(bookId) });
        } else {
          failed = true;
        }
        notify();
        reportError(err);
      },
      onBusyChange: notify,
    },
  );

  const entry: EditsEntry = {
    bookId,
    pageId,
    genId,
    push(strokes, contentPx) {
      latest = strokes.slice();
      if (contentPx) latestPx = contentPx;
      const wasFailed = failed;
      failed = false;
      // 樂觀更新：重新掛載或其他讀者看到的是畫面上的筆畫
      setEdits(latest);
      queue.push(latest);
      if (wasFailed) notify();
    },
    flush: () => queue.flush(),
    busy: () => queue.busy(),
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    get latest() {
      return latest;
    },
    get latestPx() {
      return latestPx;
    },
    get failed() {
      return failed;
    },
    shouldSeedFromLocal: () => latest !== null && (queue.busy() || failed),
    setErrorHandler(fn) {
      reportError = fn;
    },
    get autofix() {
      return autofix;
    },
    setAutofix(fn) {
      autofix = fn;
    },
  };
  map.set(key, entry);
  return entry;
}
