import type { ReactNode } from "react";
import type { CheckItem } from "@/lib/checklist";
import { ChecklistCard } from "./ChecklistCard";
import styles from "./Inspector.module.css";

/**
 * 右欄（≤ 1180 px 移到下方）：最上面是本頁進度，下面是目前模式的面板。
 * checklistLast（手機）：舞台下方先接面板（構圖卡片與「下一步」），本頁進度放最後；
 * 直接改 DOM 順序而不是 CSS order，螢幕閱讀器與 Tab 的順序才跟畫面一致（WCAG 1.3.2）。
 */
export function Inspector({
  title,
  items,
  checklistLast = false,
  children,
}: {
  title: string;
  items: CheckItem[];
  checklistLast?: boolean;
  children: ReactNode;
}) {
  const checklist = <ChecklistCard title={title} items={items} />;
  return (
    <aside aria-label="檢查與設定" className={styles.insp}>
      {!checklistLast && checklist}
      {children}
      {checklistLast && checklist}
    </aside>
  );
}
