/**
 * 修改圖層的序列化寫入（Review Focus #1）。
 *
 * 後端 PUT /pages/{id}/lineart/edits 是「整份覆蓋、最後寫入為準」，所以：
 * - 同時只有一個請求在路上；
 * - 送出期間又 push 新狀態，只保留最新的一份，前一個完成（成功或失敗）後再送；中間版本直接跳過；
 * - 因為每次送的都是畫面上的完整筆畫，最後一個送出的請求成功 ⇒ 伺服器＝畫面。
 *
 * 回報：
 * - onSuccess(qa, strokes)：只在回應對應「最新 push 的狀態」時呼叫（中間版本的 QA 已過時，不套到畫面）。
 * - onError(err, strokes)：只在「最新狀態」送失敗時呼叫；被更新狀態取代的失敗不回報（那份會被下一個請求蓋掉）。
 *   不自動重試：呼叫端決定（例如 toast 加「重試」→ 再 push 一次畫面上的筆畫）。
 * - onBusyChange(busy)：開始送 → true；佇列清空 → false（中間不閃爍）。
 *
 * 一個佇列綁一個 (page, generation)：send 由呼叫端注入並帶好 generation_id；換頁或換選圖請建新佇列。
 */
import type { Stroke } from "./strokes";

export interface WriteQueueOptions<Q> {
  onSuccess?: (qa: Q, strokes: Stroke[]) => void;
  onError?: (error: unknown, strokes: Stroke[]) => void;
  onBusyChange?: (busy: boolean) => void;
}

export interface WriteQueue<Q> {
  /** 排入畫面上目前的完整筆畫（會複製陣列）。 */
  push(strokes: Stroke[]): void;
  /**
   * 等佇列清空。成功 → 最後一次成功的 QA（從沒成功過則 undefined）；
   * 最後一次寫入失敗（伺服器與畫面不一致）→ reject 該錯誤。
   */
  flush(): Promise<Q | undefined>;
  busy(): boolean;
}

export function createWriteQueue<Q>(
  send: (strokes: Stroke[]) => Promise<Q>,
  opts: WriteQueueOptions<Q> = {},
): WriteQueue<Q> {
  let inflight = false;
  let pending: Stroke[] | null = null;
  let lastQA: Q | undefined;
  /** 最後一次（未被取代的）寫入失敗；之後任何 push 會清掉。 */
  let lastError: { error: unknown } | null = null;
  let waiters: { resolve: (q: Q | undefined) => void; reject: (e: unknown) => void }[] = [];
  /** 已通知給 onBusyChange 的狀態：只在真的轉換時通知（接續請求、回呼裡再 push 都不會閃爍或重複）。 */
  let busyNotified = false;

  const setBusy = (b: boolean) => {
    if (b === busyNotified) return;
    busyNotified = b;
    opts.onBusyChange?.(b);
  };

  const settleWaiters = () => {
    const ws = waiters;
    waiters = [];
    for (const w of ws) {
      if (lastError) w.reject(lastError.error);
      else w.resolve(lastQA);
    }
  };

  /** 送出 pending（已有請求在路上則等它結束）。 */
  const pump = () => {
    if (inflight || pending === null) return;
    const strokes = pending;
    pending = null;
    inflight = true;
    setBusy(true);
    let p: Promise<Q>;
    try {
      p = Promise.resolve(send(strokes));
    } catch (err) {
      p = Promise.reject(err);
    }
    p.then(
      (qa) => {
        lastQA = qa;
        finish(() => opts.onSuccess?.(qa, strokes));
      },
      (err) =>
        finish(() => {
          lastError = { error: err };
          opts.onError?.(err, strokes);
        }),
    );
  };

  /** 一個請求結束：有更新的狀態就接著送（這次的結果已過時，不回報）；否則回報並轉為閒置。 */
  const finish = (report: () => void) => {
    inflight = false;
    if (pending !== null) {
      pump();
      return;
    }
    try {
      report();
    } finally {
      // 回呼可能同步 push（已經有新請求在路上）：那就還不是閒置，等新請求結束再通知與結算 flush。
      // 回呼丟錯也會走到這裡，佇列不會卡在忙碌、flush 不會永遠不結束。
      if (!inflight && pending === null) {
        setBusy(false);
        settleWaiters();
      }
    }
  };

  return {
    push(strokes) {
      pending = strokes.slice();
      lastError = null;
      pump();
    },
    flush() {
      if (!inflight && pending === null) {
        return lastError ? Promise.reject(lastError.error) : Promise.resolve(lastQA);
      }
      return new Promise((resolve, reject) => waiters.push({ resolve, reject }));
    },
    busy: () => inflight || pending !== null,
  };
}
