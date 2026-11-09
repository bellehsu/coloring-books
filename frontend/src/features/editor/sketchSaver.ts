import type { QueryClient } from "@tanstack/react-query";
import { api, type Page } from "@/api/client";
import { qk } from "@/api/hooks";
import { createLatestByKey, type LatestByKey } from "@/lib/latestByKey";

/**
 * 構圖草圖存檔（PUT／DELETE /pages/{id}/sketch）：每頁依呼叫順序、只保留最新。
 * 存在模組層級（每個 QueryClient 一個），SketchCanvas 卸載後的 flush、甚至離開編輯器再回來，
 * 同一頁的寫入都不會並行或倒序。
 *
 * 也記住每頁最後一次 save 的內容（含寫入失敗的）：SketchCanvas 只在掛載時讀 initialUrl，
 * PUT 與 pages 重抓完成前重新掛載會載到舊圖，下一筆存檔就會把最後幾筆蓋掉。
 * 所以有 latest 時一律用它 seed（latestUrl），不用 page.sketch_url。
 */
export interface SketchSaver {
  /** blob＝PNG 上傳；null＝刪除草圖（畫布全白，不上傳空白圖）。 */
  save(bookId: number, pageId: number, blob: Blob | null): void;
  busy(pageId: number): boolean;
  /** 這個 session 最後 save 的內容；undefined＝沒存過（用伺服器的 sketch_url），null＝空白。 */
  latest(pageId: number): Blob | null | undefined;
  /** latest 的 object URL（同一個 blob 重複呼叫回傳同一個 URL）；null＝空白；undefined＝沒存過。 */
  latestUrl(pageId: number): string | null | undefined;
  /** 頁面刪除後清掉記憶。 */
  forget(pageId: number): void;
  setErrorHandler(fn: (error: unknown) => void): void;
}

/** 舊 object URL 延後釋放：可能有剛掛載的畫布正在載入它。 */
const REVOKE_DELAY_MS = 60_000;

const registry = new WeakMap<QueryClient, SketchSaver>();

export function sketchSaverFor(qc: QueryClient): SketchSaver {
  const found = registry.get(qc);
  if (found) return found;
  let reportError: (error: unknown) => void = () => {};
  const last = new Map<number, Blob | null>();
  const urls = new Map<number, { blob: Blob; url: string }>();

  const releaseUrl = (pageId: number) => {
    const u = urls.get(pageId);
    if (!u) return;
    urls.delete(pageId);
    setTimeout(() => URL.revokeObjectURL?.(u.url), REVOKE_DELAY_MS);
  };

  const q: LatestByKey<number, { bookId: number; blob: Blob | null }> = createLatestByKey(
    (pageId: number, { blob }: { bookId: number; blob: Blob | null }) =>
      blob ? api.upload<Page>(`/pages/${pageId}/sketch`, blob, "PUT") : api.del<Page>(`/pages/${pageId}/sketch`),
    {
      onSuccess: (_pageId, _page, { bookId }) => void qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
      onError: (_pageId, e) => reportError(e),
    },
  );
  const saver: SketchSaver = {
    save(bookId, pageId, blob) {
      last.set(pageId, blob);
      if (urls.get(pageId)?.blob !== blob) releaseUrl(pageId);
      q.push(pageId, { bookId, blob });
    },
    busy: (pageId) => q.busy(pageId),
    latest: (pageId) => last.get(pageId),
    latestUrl(pageId) {
      const b = last.get(pageId);
      if (b === undefined || b === null) return b;
      const u = urls.get(pageId);
      if (u?.blob === b) return u.url;
      const url = URL.createObjectURL(b);
      urls.set(pageId, { blob: b, url });
      return url;
    },
    forget(pageId) {
      last.delete(pageId);
      releaseUrl(pageId);
    },
    setErrorHandler(fn) {
      reportError = fn;
    },
  };
  registry.set(qc, saver);
  return saver;
}
