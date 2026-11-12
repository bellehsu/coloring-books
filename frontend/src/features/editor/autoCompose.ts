/**
 * 自動合成（§4.5）：放開拖曳、縮放、翻轉、重設位置、改選背景或角色動作之後，
 * 等一小段時間（debounce）沒有新的變化才請後端合成，一連串的變化只合成一次。
 *
 * - 每頁各自計時：在第 2 頁拖完馬上換到第 3 頁，第 2 頁照樣合成，第 3 頁不受影響。
 * - 同一頁一次只跑一個合成；跑的時候又有變化 → 跑完再合成一次（位置以最後存的為準）。
 * - 這頁的選定圖有補過的線（lineEdits 回傳記號）時，合成前先問（confirm(pageId)）；取消就不合成、回到 idle，
 *   下次有變化再問。同意只算問的當時那份補線（記號）：合成成功後就失效，之後在新合成圖上補的線，下次移動前會再問；
 *   合成失敗時，補線沒變就不再問（重試），失敗後又補了線（記號不同）就再問一次。
 * - 合成失敗停在 error，不自動重試；flush（「更新失敗，重試」）或下一次 schedule 再試。
 */
import type { AutoComposeStatus } from "@/features/editing/ComposeStage";

export interface AutoComposeOptions {
  /** 最後一次變化之後等多久才合成；預設 600 ms。 */
  delayMs?: number;
  /** 合成這一頁（等位置寫完 → POST compose）。reject = 失敗。 */
  compose: (pageId: number) => Promise<void>;
  /**
   * 這頁目前選定圖上補過的線的記號（合成會換掉選定圖，補的線跟著不見）；沒有補線回 null。
   * 補線一變（多畫一筆、換選圖）記號就要不同：同意只對問的當時那份補線有效。
   */
  lineEdits: (pageId: number) => string | null;
  /** 問使用者要不要清掉這頁補過的線；true = 繼續。 */
  confirm: (pageId: number) => Promise<boolean>;
}

export interface AutoCompose {
  /** 這頁有變化：重新開始這頁的計時。 */
  schedule(pageId: number): void;
  /** 馬上做：有計時中的就立刻合成；合成中就等它（含之後排的那次）；失敗過就重試；閒置則什麼都不做。 */
  flush(pageId: number): Promise<void>;
  status(pageId: number): AutoComposeStatus;
  /** 任何一頁狀態改變時通知（useSyncExternalStore 用）。 */
  subscribe(fn: () => void): () => void;
  /** 這個 id 不再是原本那頁（刪除、或被新頁沿用）：取消計時、清掉失敗與「已同意」。 */
  forget(pageId: number): void;
  /**
   * 編輯器掛上時呼叫（useEffect）；回傳的函式在卸載時呼叫：取消所有計時，之後的 schedule 不動作，
   * 直到再次 connect（React StrictMode 會卸載再掛上一次）。沒呼叫過 connect 時照常運作。
   */
  connect(): () => void;
}

interface PageState {
  timer: ReturnType<typeof setTimeout> | null;
  running: Promise<void> | null;
  /** 合成中又有到期的變化：跑完再合成一次。 */
  again: boolean;
  error: boolean;
  /** 使用者同意清掉的那份補線的記號（合成成功後失效）。 */
  confirmed: string | null;
}

export function createAutoCompose({ delayMs = 600, compose, lineEdits, confirm }: AutoComposeOptions): AutoCompose {
  const pages = new Map<number, PageState>();
  const listeners = new Set<() => void>();
  let active = true;
  const notify = () => listeners.forEach((fn) => fn());

  const stateOf = (pageId: number): PageState => {
    let s = pages.get(pageId);
    if (!s) {
      s = { timer: null, running: null, again: false, error: false, confirmed: null };
      pages.set(pageId, s);
    }
    return s;
  };

  const clearTimer = (s: PageState) => {
    if (s.timer != null) clearTimeout(s.timer);
    s.timer = null;
  };

  /** 一次合成（必要時先問）。 */
  const once = async (pageId: number, s: PageState) => {
    const edits = lineEdits(pageId);
    if (edits != null && edits !== s.confirmed) {
      if (!(await confirm(pageId))) return; // 取消：不合成、不算失敗
      s.confirmed = edits;
    }
    try {
      await compose(pageId);
      s.error = false;
      s.confirmed = null; // 選定圖換成新的合成圖：之後補的線要再問
    } catch {
      s.error = true;
    }
  };

  const run = (pageId: number): Promise<void> => {
    const s = stateOf(pageId);
    if (s.running) {
      s.again = true;
      return s.running;
    }
    s.error = false;
    const loop = async () => {
      try {
        do {
          s.again = false;
          await once(pageId, s);
        } while (s.again && pages.get(pageId) === s);
      } finally {
        s.running = null;
        notify();
      }
    };
    s.running = loop();
    notify();
    return s.running;
  };

  return {
    schedule(pageId) {
      if (!active) return;
      const s = stateOf(pageId);
      clearTimer(s);
      s.error = false;
      s.timer = setTimeout(() => {
        s.timer = null;
        void run(pageId);
      }, delayMs);
      notify();
    },
    flush(pageId) {
      const s = pages.get(pageId);
      if (!s) return Promise.resolve();
      if (s.timer != null) {
        clearTimer(s);
        return run(pageId);
      }
      if (s.running) return s.running;
      if (s.error && active) return run(pageId);
      return Promise.resolve();
    },
    status(pageId) {
      const s = pages.get(pageId);
      if (!s) return "idle";
      if (s.running) return "running";
      if (s.timer != null) return "pending";
      return s.error ? "error" : "idle";
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    forget(pageId) {
      const s = pages.get(pageId);
      if (!s) return;
      clearTimer(s);
      s.again = false;
      pages.delete(pageId); // 還在跑的那次照常結束，但不再影響這個 id 的新狀態
      notify();
    },
    connect() {
      active = true;
      return () => {
        active = false;
        for (const s of pages.values()) {
          clearTimer(s);
          s.again = false;
        }
        notify();
      };
    },
  };
}
