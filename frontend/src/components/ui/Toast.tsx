"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import styles from "./Toast.module.css";

export type ToastTone = "info" | "success" | "error";
interface Item {
  id: number;
  message: string;
  tone: ToastTone;
}
interface ToastApi {
  show: (message: string, tone?: ToastTone) => void;
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

  const show = useCallback((message: string, tone: ToastTone = "info") => {
    const id = nextId.current++;
    setItems((xs) => [...xs, { id, message, tone }]);
    const h = setTimeout(() => {
      timers.current.delete(h);
      setItems((xs) => xs.filter((x) => x.id !== id));
    }, DURATION_MS);
    timers.current.add(h);
  }, []);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className={styles.stack}>
        {items.map((t) => (
          <div key={t.id} role={t.tone === "error" ? "alert" : "status"} className={`${styles.toast} ${styles[t.tone]}`}>
            {t.message}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
