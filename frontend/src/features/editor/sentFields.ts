import type { QueryClient } from "@tanstack/react-query";

interface Sent<T> {
  value: T;
  p: Promise<unknown>;
}
export interface PageSent {
  scene?: Sent<string>;
  strength?: Sent<number>;
}

/**
 * 每頁最後送出的場景描述／構圖強度與它的請求，存在模組層級（每個 QueryClient 一份），比元件活得久：
 * 離開欄位（PATCH 送出中）→ 換頁或換模式 → pages 重抓前回來，欄位要顯示剛送出的值而不是舊的
 * page.scene_prompt（否則再編輯會把新值蓋回舊值）；產生前也要等前一次掛載送出的請求。
 * 背景面板（BackgroundPanel，Task 9；先前是 GeneratePanel）是這兩個欄位唯一的寫入者，所以送出成功的值一直是最新的；送出失敗就清掉（回到伺服器的值，且可重送）。
 */
const sentRegistry = new WeakMap<QueryClient, Map<number, PageSent>>();

export function sentFor(qc: QueryClient, pageId: number): PageSent {
  let m = sentRegistry.get(qc);
  if (!m) {
    m = new Map();
    sentRegistry.set(qc, m);
  }
  let e = m.get(pageId);
  if (!e) {
    e = {};
    m.set(pageId, e);
  }
  return e;
}

/** 送出並記住這一筆；失敗時（還是同一筆）清掉：回到伺服器的值，下次離開欄位或產生時會重送。 */
export function sendTracked<K extends keyof PageSent>(
  qc: QueryClient,
  pageId: number,
  key: K,
  value: NonNullable<PageSent[K]>["value"],
  send: () => Promise<unknown>,
): Promise<unknown> {
  const rec = sentFor(qc, pageId);
  const entry = { value, p: send() } as NonNullable<PageSent[K]>;
  rec[key] = entry;
  entry.p.catch(() => {
    if (rec[key] === entry) delete rec[key];
  });
  return entry.p;
}

/** 頁面刪除（或 id 被新頁沿用）：丟掉這頁送出過的值，新頁不會帶著舊的場景描述。 */
export function forgetSentFields(qc: QueryClient, pageId: number) {
  sentRegistry.get(qc)?.delete(pageId);
}
