import { useRef, useState } from "react";

/** 一次只跑一個（整段 await 期間停用按鈕；ref 擋住同一輪 render 前的第二次點擊）。 */
export function useSingleFlight() {
  const [busy, setBusy] = useState(false);
  const ref = useRef(false);
  const run = async (fn: () => Promise<unknown>) => {
    if (ref.current) return;
    ref.current = true;
    setBusy(true);
    try {
      await fn();
    } finally {
      ref.current = false;
      setBusy(false);
    }
  };
  return { busy, run };
}
