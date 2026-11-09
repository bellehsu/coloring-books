import type { QueryClient } from "@tanstack/react-query";
import { ApiError, api, type Page, type Placement } from "@/api/client";
import { qk } from "@/api/hooks";
import { createLatestByKey } from "@/lib/latestByKey";

/**
 * 角色位置存檔（PUT /pages/{id}/placement）：每頁依序、只保留最新。
 * 模組層級（每個 QueryClient 一個），拖曳中離開編輯器再回來也不會並行或倒序。
 * 確認合成前先 whenIdle(pageId)，合成才會用到畫面上的位置。
 */
export interface PlacementSaver {
  savePlacement(pageId: number, p: Placement): void;
  /** 等這頁位置寫完；最後一次寫入失敗 → reject。 */
  whenIdle(pageId: number): Promise<void>;
  /** 任何一頁有位置在送出中或待送。 */
  busy(): boolean;
  busyPage(pageId: number): boolean;
  failed(pageId: number): boolean;
  /** 重送最後 save 的位置（失敗後的「重試」）；沒存過則不動作。 */
  retry(pageId: number): void;
  /** 任何一頁送出中或失敗（算進 hasPendingWrites）。 */
  hasPending(): boolean;
  /** 這個 session 最後 save 的位置（含失敗的）；沒存過 undefined。 */
  latest(pageId: number): Placement | undefined;
  subscribe(fn: () => void): () => void;
  /** 頁面已刪除：之後的 save 丟掉，飛行中回來的 404 不報錯，並清掉記憶與失敗狀態。 */
  markGone(pageId: number): void;
  /** 這個 id 現在是新頁（後端沿用 id）：清掉已刪除標記與記憶。 */
  reset(pageId: number): void;
  setErrorHandler(fn: (error: unknown) => void): void;
}

const registry = new WeakMap<QueryClient, PlacementSaver>();

export function placementSaverFor(qc: QueryClient): PlacementSaver {
  const found = registry.get(qc);
  if (found) return found;
  let reportError: (error: unknown) => void = () => {};
  const last = new Map<number, Placement>();
  const gone = new Set<number>();

  const q = createLatestByKey<number, Placement, Page>(
    (pageId, p) => api.put<Page>(`/pages/${pageId}/placement`, p),
    {
      onSuccess: (_pageId, page) => void qc.invalidateQueries({ queryKey: qk.pages(page.book_id) }),
      onError: (pageId, e) => {
        if (gone.has(pageId) && e instanceof ApiError && e.status === 404) {
          q.clearError(pageId);
          return;
        }
        reportError(e);
      },
    },
  );

  const saver: PlacementSaver = {
    savePlacement(pageId, p) {
      if (gone.has(pageId)) return;
      last.set(pageId, p);
      q.push(pageId, p);
    },
    whenIdle: (pageId) => q.whenIdle(pageId),
    busy: () => q.anyBusy(),
    busyPage: (pageId) => q.busy(pageId),
    failed: (pageId) => q.failed(pageId),
    hasPending: () => q.anyBusy() || q.anyFailed(),
    retry(pageId) {
      const p = last.get(pageId);
      if (p && !gone.has(pageId)) q.push(pageId, p);
    },
    latest: (pageId) => last.get(pageId),
    subscribe: (fn) => q.subscribe(fn),
    markGone(pageId) {
      gone.add(pageId);
      last.delete(pageId);
      q.clearError(pageId);
    },
    reset(pageId) {
      gone.delete(pageId);
      last.delete(pageId);
      q.clearError(pageId);
    },
    setErrorHandler(fn) {
      reportError = fn;
    },
  };
  registry.set(qc, saver);
  return saver;
}
