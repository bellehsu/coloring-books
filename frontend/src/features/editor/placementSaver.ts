import type { QueryClient } from "@tanstack/react-query";
import { ApiError, api, type Page, type Placement } from "@/api/client";
import { qk } from "@/api/hooks";
import { createLatestByKey } from "@/lib/latestByKey";

/**
 * 角色位置存檔（PUT /pages/{id}/placement）：每頁依序、只保留最新。
 * 模組層級（每個 QueryClient 一個），拖曳中離開編輯器再回來也不會並行或倒序。
 * 合成前先 whenIdle(pageId)（useCompose 內），合成才會用到畫面上的位置。
 */
export interface PlacementSaver {
  savePlacement(pageId: number, p: Placement): void;
  /** 等這頁位置寫完；最後一次寫入失敗 → reject。 */
  whenIdle(pageId: number): Promise<void>;
  failed(pageId: number): boolean;
  /** 重送最後 save 的位置（失敗後的「重試」）；沒存過則不動作。 */
  retry(pageId: number): void;
  /** 有給 pageId：這頁送出中或失敗；沒給：任何一頁（算進 hasPendingWrites）。 */
  hasPending(pageId?: number): boolean;
  /**
   * 這頁還沒存好的位置：送出中或失敗時是最後 save 的值，否則 undefined。
   * 存好了伺服器就是真相（C 改過位置、復原也看得到），畫面用 page.placement（同 editsQueue 的 shouldSeedFromLocal）。
   */
  latest(pageId: number): Placement | undefined;
  subscribe(fn: () => void): () => void;
  /** 頁面已刪除：之後的 save 丟掉，飛行中回來的 404 不報錯，並清掉記憶與失敗狀態。 */
  markGone(pageId: number): void;
  /** 這頁已刪除（markGone 之後、reset 之前）。編輯器用來略過已刪除頁的自動合成，並認出被新頁沿用的 id。 */
  isGone(pageId: number): boolean;
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
      onSuccess: (_pageId, page) => {
        // 先寫進頁面快取：latest 接著清掉，畫面改讀 page.placement 時不會先跳回舊位置
        qc.setQueryData<Page[]>(qk.pages(page.book_id), (old) => old?.map((p) => (p.id === page.id ? page : p)));
        void qc.invalidateQueries({ queryKey: qk.pages(page.book_id) });
      },
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
    failed: (pageId) => q.failed(pageId),
    hasPending: (pageId) => (pageId == null ? q.anyBusy() || q.anyFailed() : q.busy(pageId) || q.failed(pageId)),
    retry(pageId) {
      const p = last.get(pageId);
      if (p && !gone.has(pageId)) q.push(pageId, p);
    },
    latest: (pageId) => (saver.hasPending(pageId) ? last.get(pageId) : undefined),
    subscribe: (fn) => q.subscribe(fn),
    markGone(pageId) {
      gone.add(pageId);
      last.delete(pageId);
      q.clearError(pageId);
    },
    isGone: (pageId) => gone.has(pageId),
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
