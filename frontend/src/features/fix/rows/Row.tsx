import { useId, type ReactNode } from "react";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import { usePatchPageC } from "../fixApi";
import styles from "../FixPage.module.css";

export const quiet = (p: Promise<unknown>) => p.catch(() => {});

/** 修改列的外框：色點、標題與一行說明、右邊的按鈕，需要時加「↺ 還原成自動畫的那張」。 */
export function Row({
  title,
  tint,
  sub,
  actions,
  restore,
  children,
}: {
  title: string;
  tint: "pink" | "sky" | "yellow" | "green";
  sub: ReactNode;
  actions: ReactNode;
  restore: (() => void) | null;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <li aria-labelledby={id} className={styles.row}>
      <div className={styles.rowHead}>
        <span aria-hidden="true" className={`${styles.dot} ${styles[`dot_${tint}`]}`} />
        <div className={styles.rowText}>
          <h3 id={id} className={styles.rowTitle}>
            {title}
          </h3>
          <p className={styles.rowSub}>{sub}</p>
        </div>
        <div className={styles.rowActions}>{actions}</div>
      </div>
      {restore && (
        <button type="button" className={styles.restore} onClick={restore}>
          <span aria-hidden="true">↺</span> 還原成自動畫的那張
        </button>
      )}
      {children}
    </li>
  );
}

/** 存頁面文字（PATCH ?oneclick=true）；失敗 toast 並 reject。 */
export function useSavePage(bookId: number, pageId: number) {
  const toast = useToast();
  const patch = usePatchPageC(bookId, pageId);
  return (body: {
    scene_prompt?: string;
    action_prompt?: string;
    caption_zh?: string;
    caption_en?: string;
    caption_show_zh?: boolean;
    caption_show_en?: boolean;
  }) =>
    patch.mutateAsync(body).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });
}
