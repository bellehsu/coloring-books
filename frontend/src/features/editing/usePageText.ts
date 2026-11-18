import { useRef, useState } from "react";

/**
 * 頁面上的一個文字欄位：沒改過就顯示伺服器的值；改過的值在 save() 時送出（同一個值不重送，
 * 送出中的請求會被等待）。失敗時 toast，save() reject，下次會重送。
 * resetKey 變了（C 的復原／重做／還原之後）就丟掉改過的值、改顯示伺服器的值，之前送過的也不再算「已送」。
 */
export function usePageText(serverValue: string, send: (v: string) => Promise<unknown>, resetKey: number = 0) {
  const [edited, setEdited] = useState<string | null>(null);
  const [key, setKey] = useState(resetKey);
  const sent = useRef<{ value: string; p: Promise<unknown>; key: number; settled: boolean } | null>(null);
  /** 最後送出的值與它是否已經送完（render 裡判斷用；ref 不能在 render 中讀）。 */
  const [lastSent, setLastSent] = useState<{ value: string; key: number; settled: boolean } | null>(null);
  const lastLive = lastSent?.key === resetKey ? lastSent : null;
  if (key !== resetKey) {
    setKey(resetKey);
    setEdited(null);
  } else if (edited !== null && edited === serverValue && (!lastLive || (lastLive.settled && lastLive.value === edited))) {
    // 伺服器追上了（存好後重抓）：回到顯示伺服器的值，之後別處改的文字才看得到。
    // 還有別的值在送（例如送出中又改回原本的字）時不算追上，留著改過的值等 save() 送出
    setEdited(null);
  }
  const value = key !== resetKey ? serverValue : (edited ?? serverValue);
  /** 這一輪（resetKey）送過的；復原之前送的不算，同樣的文字再打一次要重送。 */
  const live = () => (sent.current?.key === resetKey ? sent.current : null);

  // 送出一律串在前一筆之後（忽略前一筆的失敗），伺服器依點擊順序收到，最後落地的一定是最新的文字。
  const enqueue = (v: string): Promise<unknown> => {
    const last = live();
    if (last?.value === v && !last.settled) return last.p; // 同一個值還在送：不重送
    const prev = sent.current?.p;
    const p = (prev ? prev.catch(() => {}) : Promise.resolve()).then(() => send(v));
    const entry = { value: v, p, key: resetKey, settled: false };
    sent.current = entry;
    setLastSent({ value: v, key: resetKey, settled: false });
    p.then(
      () => {
        entry.settled = true;
        if (sent.current === entry) setLastSent({ value: v, key: entry.key, settled: true });
      },
      () => {
        if (sent.current === entry) {
          sent.current = null;
          setLastSent(null);
        }
      },
    );
    return p;
  };

  const save = (): Promise<unknown> => {
    if (edited === null || key !== resetKey) return Promise.resolve();
    const last = live();
    // 伺服器已經是這個值（沒送過別的，或最後送的就是它而且送完了）：不必送
    if (edited === serverValue && (!last || (last.settled && last.value === edited))) return Promise.resolve();
    return enqueue(edited);
  };
  /** 設定值並立刻送出（晶片點擊用）；送的是 v，不是還沒更新的 edited。 */
  const setNow = (v: string): Promise<unknown> => {
    setEdited(v);
    if (!live() && v === serverValue) return Promise.resolve();
    return enqueue(v);
  };
  return { value, setValue: setEdited, setNow, save };
}
