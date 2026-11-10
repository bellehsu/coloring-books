import { Icons } from "@/components/ui/Icons";
import type { CheckItem, CheckState } from "@/lib/checklist";
import styles from "./Inspector.module.css";

const SR: Record<CheckState, string> = {
  done: "完成",
  todo: "未完成",
  warn: "需要處理",
  skip: "略過",
};

/** 「第 N 頁進度」：checklist(page, qa) 的 3 項（構圖、補線、完成）。 */
export function ChecklistCard({ title, items }: { title: string; items: CheckItem[] }) {
  return (
    <section aria-label="本頁進度" className={styles.card}>
      <b className={styles.cardTitle}>{title}</b>
      <ul className={styles.checks}>
        {items.map((it) => (
          <li key={it.key} className={styles.check} data-state={it.state}>
            <span className={styles.mark} data-state={it.state} aria-hidden="true">
              {it.state === "done" && <Icons.check size={12} />}
              {it.state === "warn" && "!"}
              {it.state === "skip" && "–"}
            </span>
            <span className={styles.checkLabel}>
              {it.label}
              <span className={styles.srOnly}>（{SR[it.state]}）</span>
            </span>
            {it.note && <span className={styles.note}>{it.note}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
