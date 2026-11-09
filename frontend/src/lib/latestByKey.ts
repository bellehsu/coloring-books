/**
 * 依 key 序列化的「最新值」寫入器（草圖 PUT／DELETE 用）。
 *
 * - 同一個 key 同時最多一個請求；送出中再 push 只保留最新的值，前一個完成（成功或失敗）後送出它。
 *   每次 push 都是完整狀態，所以中間值可略過，最後一次成功的寫入就等於最後 push 的值。
 * - 不同 key 互不阻擋。
 * - onSuccess／onError 只在完成的是該 key 目前最新的值時呼叫（被取代的寫入結果沒有意義）。
 * - 不自動重試；send 同步丟錯或 callback 丟錯都不會讓 key 卡在忙碌。
 */
export interface LatestByKeyOptions<K, V, R> {
  onSuccess?: (key: K, result: R, value: V) => void;
  onError?: (key: K, error: unknown, value: V) => void;
}

export interface LatestByKey<K, V> {
  push(key: K, value: V): void;
  /** 這個 key 有請求送出中或待送。 */
  busy(key: K): boolean;
}

interface Slot<V> {
  inflight: boolean;
  pending: { value: V } | null;
}

export function createLatestByKey<K, V, R>(
  send: (key: K, value: V) => Promise<R>,
  opts: LatestByKeyOptions<K, V, R> = {},
): LatestByKey<K, V> {
  const slots = new Map<K, Slot<V>>();

  const safe = (fn: () => void) => {
    try {
      fn();
    } catch {
      /* callback 的錯誤不能卡住佇列 */
    }
  };

  function run(key: K, slot: Slot<V>) {
    if (slot.inflight || !slot.pending) return;
    const { value } = slot.pending;
    slot.pending = null;
    slot.inflight = true;
    let p: Promise<R>;
    try {
      p = send(key, value);
    } catch (e) {
      p = Promise.reject(e);
    }
    p.then(
      (r) => {
        if (!slot.pending) safe(() => opts.onSuccess?.(key, r, value));
      },
      (e) => {
        if (!slot.pending) safe(() => opts.onError?.(key, e, value));
      },
    ).finally(() => {
      slot.inflight = false;
      if (slot.pending) run(key, slot);
      else if (slots.get(key) === slot) slots.delete(key);
    });
  }

  return {
    push(key, value) {
      let slot = slots.get(key);
      if (!slot) {
        slot = { inflight: false, pending: null };
        slots.set(key, slot);
      }
      slot.pending = { value };
      run(key, slot);
    },
    busy(key) {
      const s = slots.get(key);
      return !!s && (s.inflight || !!s.pending);
    },
  };
}
