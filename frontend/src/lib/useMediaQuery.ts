import { useCallback, useSyncExternalStore } from "react";

/** 手機寬度（§4.5）：頁面清單改成下拉選單、構圖卡片改成舞台下方的精簡卡片。 */
export const PHONE_QUERY = "(max-width: 640px)";

const mql = (query: string) =>
  typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query) : null;

/** CSS media query 目前是否成立；伺服器端與沒有 matchMedia 的環境一律 false（桌機版面）。 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const m = mql(query);
      if (!m) return () => {};
      m.addEventListener("change", onChange);
      return () => m.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => mql(query)?.matches ?? false,
    () => false,
  );
}
