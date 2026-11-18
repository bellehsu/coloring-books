import { useRef, useState } from "react";

/**
 * 頁面上的一個文字欄位：沒改過就顯示伺服器的值；改過的值在 save() 時送出（同一個值不重送，
 * 送出中的請求會被等待）。失敗時 toast，save() reject，下次會重送。
 * resetKey 變了（C 的復原／重做／還原之後）就丟掉改過的值、改顯示伺服器的值，之前送過的也不再算「已送」。
 */
export function usePageText(serverValue: string, send: (v: string) => Promise<unknown>, resetKey: number = 0) {
  const [edited, setEdited] = useState<string | null>(null);
  const [key, setKey] = useState(resetKey);
  const sent = useRef<{ value: string; p: Promise<unknown>; key: number } | null>(null);
  if (key !== resetKey) {
    setKey(resetKey);
    setEdited(null);
  } else if (edited !== null && edited === serverValue) {
    // 伺服器追上了（存好後重抓）：回到顯示伺服器的值，之後別處改的文字才看得到
    setEdited(null);
  }
  const value = key !== resetKey ? serverValue : (edited ?? serverValue);
  /** 這一輪（resetKey）送過的；復原之前送的不算，同樣的文字再打一次要重送。 */
  const live = () => (sent.current?.key === resetKey ? sent.current : null);

  // 送出一律串在前一筆之後（忽略前一筆的失敗），伺服器依點擊順序收到，最後落地的一定是最新的文字。
  const enqueue = (v: string): Promise<unknown> => {
    const last = live();
    if (last?.value === v) return last.p;
    const prev = sent.current?.p;
    const p = (prev ? prev.catch(() => {}) : Promise.resolve()).then(() => send(v));
    const entry = { value: v, p, key: resetKey };
    sent.current = entry;
    p.catch(() => {
      if (sent.current === entry) sent.current = null;
    });
    return p;
  };

  const save = (): Promise<unknown> => {
    if (edited === null || key !== resetKey) return Promise.resolve();
    const last = live();
    if (last?.value !== edited && !last && edited === serverValue) return Promise.resolve();
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
