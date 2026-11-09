import type { ReactNode } from "react";
import type { CheckItem } from "@/lib/checklist";
import { ChecklistCard } from "./ChecklistCard";
import styles from "./Inspector.module.css";

/** 右欄（≤ 1180 px 移到下方）：最上面是本頁進度，下面是目前模式的面板。 */
export function Inspector({ title, items, children }: { title: string; items: CheckItem[]; children: ReactNode }) {
  return (
    <aside aria-label="檢查與設定" className={styles.insp}>
      <ChecklistCard title={title} items={items} />
      {children}
    </aside>
  );
}
