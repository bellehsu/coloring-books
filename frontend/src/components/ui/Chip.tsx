import type { ReactNode } from "react";
import styles from "./Chip.module.css";

export function Chip({ tone, children }: { tone: "ok" | "warn" | "draft" | "busy"; children: ReactNode }) {
  return (
    <span className={`${styles.chip} ${styles[tone]}`}>
      <span className={styles.dot} aria-hidden="true" />
      {children}
    </span>
  );
}
