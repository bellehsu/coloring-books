/**
 * 依 key 序列化的「最新值」寫入器（草圖 PUT／DELETE 用）。
 *
 * - 同一個 key 同時最多一個請求；送出中再 push 只保留最新的值，前一個完成（成功或失敗）後送出它。
 *   每次 push 都是完整狀態，所以中間值可略過，最後一次成功的寫入就等於最後 push 的值。
 * - 不同 key 互不阻擋。
 * - onSuccess／onError 只在完成的是該 key 目前最新的值時呼叫（被取代的寫入結果沒有意義）。
 * - 不自動重試；send 同步丟錯或 callback 丟錯都不會讓 key 卡在忙碌。
 * - 最新值寫入失敗是「持續的狀態」（failed／whenIdle reject），直到下一次 push 或 clearError。
 */
export interface LatestByKeyOptions<K, V, R> {
  onSuccess?: (key: K, result: R, value: V) => void;
  onError?: (key: K, error: unknown, value: V) => void;
}

export interface LatestByKey<K, V> {
  push(key: K, value: V): void;
  /** 這個 key 有請求送出中或待送。 */
  busy(key: K): boolean;
  /** 這個 key 最後一次（未被取代的）寫入失敗，且之後沒有再 push。 */
  failed(key: K): boolean;
  /** 等這個 key 閒置：最後一筆成功（或從沒寫過）→ resolve；最後一筆失敗 → reject 該錯誤。 */
  whenIdle(key: K): Promise<void>;
  /** 忘掉這個 key 的失敗（例如頁面已刪除）。送出中的請求照常結束。 */
  clearError(key: K): void;
  anyBusy(): boolean;
  anyFailed(): boolean;
  /** 忙碌／失敗狀態變化時通知（useSyncExternalStore 用）。 */
  subscribe(fn: () => void): () => void;
}

interface Slot<V> {
  inflight: boolean;
  pending: { value: V } | null;
}

interface Waiter {
  resolve: () => void;
  reject: (e: unknown) => void;
}

export function createLatestByKey<K, V, R>(
  send: (key: K, value: V) => Promise<R>,
  opts: LatestByKeyOptions<K, V, R> = {},
): LatestByKey<K, V> {
  const slots = new Map<K, Slot<V>>();
  const errors = new Map<K, unknown>();
  const waiters = new Map<K, Waiter[]>();
  const listeners = new Set<() => void>();

  const safe = (fn: () => void) => {
    try {
      fn();
    } catch {
      /* callback 的錯誤不能卡住佇列 */
    }
  };
  const notify = () => listeners.forEach((fn) => safe(fn));

  const isBusy = (key: K) => {
    const s = slots.get(key);
    return !!s && (s.inflight || !!s.pending);
  };

  const settle = (key: K) => {
    const ws = waiters.get(key);
    if (!ws) return;
    waiters.delete(key);
    const failed = errors.has(key);
    for (const w of ws) {
      if (failed) w.reject(errors.get(key));
      else w.resolve();
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
        if (slot.pending) return;
        errors.set(key, e);
        safe(() => opts.onError?.(key, e, value));
      },
    ).finally(() => {
      slot.inflight = false;
      if (slot.pending) {
        run(key, slot);
        return;
      }
      if (slots.get(key) === slot) slots.delete(key);
      settle(key);
      notify();
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
      errors.delete(key);
      run(key, slot);
      notify();
    },
    busy: isBusy,
    failed: (key) => errors.has(key),
    whenIdle(key) {
      if (!isBusy(key)) return errors.has(key) ? Promise.reject(errors.get(key)) : Promise.resolve();
      return new Promise<void>((resolve, reject) => {
        const ws = waiters.get(key) ?? [];
        ws.push({ resolve, reject });
        waiters.set(key, ws);
      });
    },
    clearError(key) {
      if (errors.delete(key)) notify();
    },
    anyBusy: () => [...slots.keys()].some(isBusy),
    anyFailed: () => errors.size > 0,
    subscribe(fn) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
}
