"use client";

import type { Page, Placement } from "@/api/client";
import styles from "./Inspector.module.css";

export interface ComposePanelProps {
  /** 呼叫端用 key={page.id}。 */
  page: Page;
  /** 目前位置（同 ComposeStage）。 */
  placement: Placement;
  /** 改位置（水平翻轉、重設位置）；EditorPage 自動存。 */
  onPlacementChange: (p: Placement) => void;
  /**
   * 確認合成：EditorPage 先等舊選圖的修線寫完，useCompose 再等位置寫完（placementSaver.whenIdle），
   * 然後 POST /compose；成功後切到「線稿與修線」。失敗時 EditorPage 顯示 toast，promise 照常 resolve。
   */
  onConfirm: () => Promise<unknown>;
  /** 確認合成整段流程進行中（停用按鈕防連點）。 */
  confirming: boolean;
}

/**
 * 合成模式的面板。
 * Task 8 佔位。Task 11 換成完整版：水平翻轉、重設位置、「確認合成」。
 */
export function ComposePanel(props: ComposePanelProps) {
  return (
    <section aria-label="合成設定" className={styles.panel} data-page={props.page.id}>
      <h2 className={styles.h2}>合成</h2>
    </section>
  );
}
