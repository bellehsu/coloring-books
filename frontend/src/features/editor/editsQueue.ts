import type { QueryClient } from "@tanstack/react-query";
import { api, type QA } from "@/api/client";
import { qk } from "@/api/hooks";
import type { Stroke } from "@/lib/strokes";
import { createWriteQueue, type WriteQueue } from "@/lib/writeQueue";

/**
 * 每個（QueryClient, 頁, 選圖）只有一個修線寫入佇列，存在模組層級，比元件活得久：
 * 換模式／換頁再回來（元件重新掛載）時沿用同一個佇列，舊請求還在路上時新的不會並行送出，
 * 「同一頁的修改圖層依序寫入」在重新掛載後仍成立。
 */
export interface EditsEntry {
  push(strokes: Stroke[]): void;
  flush(): Promise<QA | undefined>;
  busy(): boolean;
  /** 訂閱忙碌狀態變化（useSyncExternalStore 用）。 */
  subscribe(fn: () => void): () => void;
  /** 最後 push 的完整筆畫（尚未確認也算）；這個 session 沒 push 過為 null。 */
  readonly latest: Stroke[] | null;
  /** 最新一次寫入失敗（伺服器與畫面不一致），下一次 push 清除。 */
  readonly failed: boolean;
  /**
   * 重新掛載時要用 latest 而不是伺服器的 edits：佇列忙碌（伺服器還沒追上）或最後一次寫入失敗。
   * 閒置且成功時伺服器就是真相，從 useEdits seed。
   */
  shouldSeedFromLocal(): boolean;
  /** 設定寫入失敗的回報（目前掛載的元件換成自己的 toast）。 */
  setErrorHandler(fn: (error: unknown) => void): void;
}

const registry = new WeakMap<QueryClient, Map<string, EditsEntry>>();

export function editsQueueFor(qc: QueryClient, bookId: number, pageId: number, genId: number): EditsEntry {
  let map = registry.get(qc);
  if (!map) {
    map = new Map();
    registry.set(qc, map);
  }
  const key = `${pageId}:${genId}`;
  const found = map.get(key);
  if (found) return found;

  const listeners = new Set<() => void>();
  let latest: Stroke[] | null = null;
  let failed = false;
  let reportError: (error: unknown) => void = () => {};

  const queue: WriteQueue<QA> = createWriteQueue<QA>(
    (strokes) => api.put<QA>(`/pages/${pageId}/lineart/edits`, { generation_id: genId, strokes }),
    {
      onSuccess: (qa) => {
        failed = false;
        qc.setQueryData(qk.qa(pageId), qa);
        // lineart_url／needs_fix／status（修線會把通過退回草稿）與整本統計
        void qc.invalidateQueries({ queryKey: qk.pages(bookId) });
        void qc.invalidateQueries({ queryKey: qk.book(bookId) });
        void qc.invalidateQueries({ queryKey: qk.books() });
        void qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) });
      },
      onError: (err) => {
        failed = true;
        reportError(err);
      },
      onBusyChange: () => listeners.forEach((fn) => fn()),
    },
  );

  const entry: EditsEntry = {
    push(strokes) {
      latest = strokes.slice();
      failed = false;
      // 樂觀更新：重新掛載或其他讀者看到的是畫面上的筆畫
      qc.setQueryData(qk.edits(pageId), { generation_id: genId, strokes: latest });
      queue.push(latest);
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
    get failed() {
      return failed;
    },
    shouldSeedFromLocal: () => latest !== null && (queue.busy() || failed),
    setErrorHandler(fn) {
      reportError = fn;
    },
  };
  map.set(key, entry);
  return entry;
}
