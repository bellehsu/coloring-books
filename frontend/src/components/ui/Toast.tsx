"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import styles from "./Toast.module.css";

export type ToastTone = "info" | "success" | "error";
interface Item {
  id: number;
  message: string;
  tone: ToastTone;
  action?: ToastAction;
}
export interface ToastAction {
  label: string;
  onClick: () => void;
}
interface ToastApi {
  /** action：訊息旁的按鈕（例如「復原」）；按下去執行 onClick 並關掉這則。 */
  show: (message: string, tone?: ToastTone, action?: ToastAction) => void;
}

const DURATION_MS = 5000;
const Ctx = createContext<ToastApi>({ show: () => {} });

export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const t = timers.current;
    return () => t.forEach(clearTimeout);
  }, []);

  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);

  const show = useCallback((message: string, tone: ToastTone = "info", action?: ToastAction) => {
    const id = nextId.current++;
    setItems((xs) => [...xs, { id, message, tone, action }]);
    const h = setTimeout(() => {
      timers.current.delete(h);
      dismiss(id);
    }, DURATION_MS);
    timers.current.add(h);
  }, [dismiss]);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <Ctx.Provider value={api}>
      {children}
      {/* 常駐的 live region（一開始就在、空的）：訊息是加進去的，螢幕閱讀器才會唸。
          一個一個插入已經填好字的 role=status／alert 節點，很多螢幕閱讀器不會唸。 */}
      <div className={styles.stack}>
        <div aria-live="polite" aria-relevant="additions text" data-testid="toast-polite" className={styles.region}>
          {items.filter((t) => t.tone !== "error").map((t) => (
            <ToastItem key={t.id} item={t} onDismiss={dismiss} />
          ))}
        </div>
        <div aria-live="assertive" aria-relevant="additions text" data-testid="toast-assertive" className={styles.region}>
          {items.filter((t) => t.tone === "error").map((t) => (
            <ToastItem key={t.id} item={t} onDismiss={dismiss} />
          ))}
        </div>
      </div>
    </Ctx.Provider>
  );
}

function ToastItem({ item, onDismiss }: { item: Item; onDismiss: (id: number) => void }) {
  const { action } = item;
  return (
    <div className={`${styles.toast} ${styles[item.tone]}`}>
      {item.message}
      {action && (
        <button
          type="button"
          className={styles.action}
          onClick={() => {
            action.onClick();
            onDismiss(item.id);
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
