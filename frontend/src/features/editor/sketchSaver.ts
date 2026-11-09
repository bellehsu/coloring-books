import type { QueryClient } from "@tanstack/react-query";
import { api, type Page } from "@/api/client";
import { qk } from "@/api/hooks";
import { createLatestByKey, type LatestByKey } from "@/lib/latestByKey";

/**
 * 構圖草圖存檔（PUT／DELETE /pages/{id}/sketch）：每頁依呼叫順序、只保留最新。
 * 存在模組層級（每個 QueryClient 一個），SketchCanvas 卸載後的 flush、甚至離開編輯器再回來，
 * 同一頁的寫入都不會並行或倒序。
 */
export interface SketchSaver {
  /** blob＝PNG 上傳；null＝刪除草圖（畫布全白，不上傳空白圖）。 */
  save(bookId: number, pageId: number, blob: Blob | null): void;
  busy(pageId: number): boolean;
  setErrorHandler(fn: (error: unknown) => void): void;
}

const registry = new WeakMap<QueryClient, SketchSaver>();

export function sketchSaverFor(qc: QueryClient): SketchSaver {
  const found = registry.get(qc);
  if (found) return found;
  let reportError: (error: unknown) => void = () => {};
  const q: LatestByKey<number, { bookId: number; blob: Blob | null }> = createLatestByKey(
    (pageId: number, { blob }: { bookId: number; blob: Blob | null }) =>
      blob ? api.upload<Page>(`/pages/${pageId}/sketch`, blob, "PUT") : api.del<Page>(`/pages/${pageId}/sketch`),
    {
      onSuccess: (_pageId, _page, { bookId }) => void qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
      onError: (_pageId, e) => reportError(e),
    },
  );
  const saver: SketchSaver = {
    save: (bookId, pageId, blob) => q.push(pageId, { bookId, blob }),
    busy: (pageId) => q.busy(pageId),
    setErrorHandler(fn) {
      reportError = fn;
    },
  };
  registry.set(qc, saver);
  return saver;
}
