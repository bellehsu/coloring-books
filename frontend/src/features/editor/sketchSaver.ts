import type { QueryClient } from "@tanstack/react-query";
import { ApiError, api, type Page } from "@/api/client";
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
 *
 * 掛載中的 SketchCanvas 會 register 一個 handle：產生候選、匯出前可以 flush（不等 1 秒 debounce），
 * 離開頁面前也能知道畫布還有沒存的變更（hasPending）。
 */
export interface SketchCanvasHandle {
  /** 有未存變更就立即（同步）呼叫 onSave。 */
  flush(): void;
  /** 有未存變更（debounce 還沒到，或 PNG 編碼中）。 */
  dirty(): boolean;
}

export interface SketchSaver {
  /** blob＝PNG 上傳；null＝刪除草圖（畫布全白，不上傳空白圖）。 */
  save(bookId: number, pageId: number, blob: Blob | null): void;
  busy(pageId: number): boolean;
  /** 最後一次寫入失敗（伺服器與畫布不一致），直到下一次 save／retry。 */
  failed(pageId: number): boolean;
  /** 重送：畫布有未存變更就先 flush；仍是失敗狀態就再送一次最後 save 的內容。 */
  retry(pageId: number): void;
  /** 叫這頁掛載中的畫布立即存檔（沒有未存變更則不動作）。 */
  flush(pageId: number): void;
  /** 等這頁閒置；最後一次寫入失敗 → reject。 */
  whenIdle(pageId: number): Promise<void>;
  /** flush 這本書所有掛載中的畫布，再等這本書每一頁的寫入；任何一頁失敗 → reject 第一個錯誤。 */
  whenBookIdle(bookId: number): Promise<void>;
  /** SketchCanvas 掛載時登記；回傳取消登記。 */
  register(bookId: number, pageId: number, handle: SketchCanvasHandle): () => void;
  /** 任何一頁寫入中、失敗，或有畫布有未存變更（離開頁面前提醒用）。 */
  hasPending(): boolean;
  /** busy／failed 變化時通知（useSyncExternalStore 用）。 */
  subscribe(fn: () => void): () => void;
  /** 這個 session 最後 save 的內容；undefined＝沒存過（用伺服器的 sketch_url），null＝空白。 */
  latest(pageId: number): Blob | null | undefined;
  /** latest 的 object URL（同一個 blob 重複呼叫回傳同一個 URL）；null＝空白；undefined＝沒存過。 */
  latestUrl(pageId: number): string | null | undefined;
  /** 頁面刪除後清掉記憶與失敗狀態。 */
  forget(pageId: number): void;
  /**
   * 開始刪除這頁（在切到相鄰頁「之前」呼叫）：之後的 save（例如畫布卸載 flush）先停著不送，
   * 不會和 DELETE 賽跑、也不會跳出誤導的「草圖儲存失敗」。
   */
  beginDelete(pageId: number): void;
  /** 刪除結束：成功 → forget，飛行中寫入回來的 404 不回報；失敗 → 停著的存檔照常送出。 */
  endDelete(pageId: number, deleted: boolean): void;
  /** 這頁已刪除（endDelete 成功），之後的 save 會被丟掉。 */
  isGone(pageId: number): boolean;
  /**
   * 這個 id 現在是新頁（後端沿用了已刪除頁的 id）：清掉已刪除標記、刪除中停著的存檔與記憶，
   * 之後的 save 照常送出。
   */
  reset(pageId: number): void;
  setErrorHandler(fn: (error: unknown) => void): void;
}

/** 舊 object URL 延後釋放：可能有剛掛載的畫布正在載入它。 */
const REVOKE_DELAY_MS = 60_000;

const registry = new WeakMap<QueryClient, SketchSaver>();

type Value = { bookId: number; blob: Blob | null };

export function sketchSaverFor(qc: QueryClient): SketchSaver {
  const found = registry.get(qc);
  if (found) return found;
  let reportError: (error: unknown) => void = () => {};
  const last = new Map<number, Value>();
  const urls = new Map<number, { blob: Blob; url: string }>();
  const canvases = new Map<number, Set<{ bookId: number; handle: SketchCanvasHandle }>>();
  /** 刪除中的頁：save 先停在這裡（只留最新）。 */
  const deleting = new Map<number, Value | null>();
  /** 已刪除的頁：之後回來的 404 不算失敗。 */
  const gone = new Set<number>();

  const releaseUrl = (pageId: number) => {
    const u = urls.get(pageId);
    if (!u) return;
    urls.delete(pageId);
    setTimeout(() => URL.revokeObjectURL?.(u.url), REVOKE_DELAY_MS);
  };

  const q: LatestByKey<number, Value> = createLatestByKey(
    (pageId: number, { blob }: Value) =>
      blob ? api.upload<Page>(`/pages/${pageId}/sketch`, blob, "PUT") : api.del<Page>(`/pages/${pageId}/sketch`),
    {
      onSuccess: (_pageId, _page, { bookId }) => void qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
      onError: (pageId, e) => {
        if ((gone.has(pageId) || deleting.has(pageId)) && e instanceof ApiError && e.status === 404) {
          q.clearError(pageId);
          return;
        }
        reportError(e);
      },
    },
  );

  const handlesOf = (pageId: number) => [...(canvases.get(pageId) ?? [])];

  const saver: SketchSaver = {
    save(bookId, pageId, blob) {
      if (deleting.has(pageId)) {
        deleting.set(pageId, { bookId, blob });
        return;
      }
      if (gone.has(pageId)) return;
      last.set(pageId, { bookId, blob });
      if (urls.get(pageId)?.blob !== blob) releaseUrl(pageId);
      q.push(pageId, { bookId, blob });
    },
    busy: (pageId) => q.busy(pageId),
    failed: (pageId) => q.failed(pageId),
    retry(pageId) {
      saver.flush(pageId);
      const v = last.get(pageId);
      if (q.failed(pageId) && v) q.push(pageId, v);
    },
    flush(pageId) {
      for (const c of handlesOf(pageId)) c.handle.flush();
    },
    whenIdle: (pageId) => q.whenIdle(pageId),
    async whenBookIdle(bookId) {
      for (const [, set] of canvases) for (const c of set) if (c.bookId === bookId) c.handle.flush();
      const pages = [...last].filter(([, v]) => v.bookId === bookId).map(([pageId]) => pageId);
      const results = await Promise.allSettled(pages.map((p) => q.whenIdle(p)));
      const bad = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
      if (bad) throw bad.reason;
    },
    register(bookId, pageId, handle) {
      const entry = { bookId, handle };
      let set = canvases.get(pageId);
      if (!set) {
        set = new Set();
        canvases.set(pageId, set);
      }
      set.add(entry);
      return () => {
        const s = canvases.get(pageId);
        s?.delete(entry);
        if (s?.size === 0) canvases.delete(pageId);
      };
    },
    hasPending() {
      if (q.anyBusy() || q.anyFailed()) return true;
      for (const [pageId, set] of canvases) {
        if (deleting.has(pageId) || gone.has(pageId)) continue;
        for (const c of set) if (c.handle.dirty()) return true;
      }
      return false;
    },
    subscribe: (fn) => q.subscribe(fn),
    latest: (pageId) => {
      const v = last.get(pageId);
      return v === undefined ? undefined : v.blob;
    },
    latestUrl(pageId) {
      const b = saver.latest(pageId);
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
      q.clearError(pageId);
    },
    beginDelete(pageId) {
      if (!deleting.has(pageId)) deleting.set(pageId, null);
    },
    endDelete(pageId, deleted) {
      const parked = deleting.get(pageId);
      deleting.delete(pageId);
      if (deleted) {
        gone.add(pageId);
        saver.forget(pageId);
      } else if (parked) {
        saver.save(parked.bookId, pageId, parked.blob);
      }
    },
    isGone: (pageId) => gone.has(pageId),
    reset(pageId) {
      gone.delete(pageId);
      deleting.delete(pageId);
      saver.forget(pageId);
    },
    setErrorHandler(fn) {
      reportError = fn;
    },
  };
  registry.set(qc, saver);
  return saver;
}
