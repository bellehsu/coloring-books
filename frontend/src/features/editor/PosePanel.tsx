"use client";

import type { Page } from "@/api/client";
import styles from "./Inspector.module.css";

export interface PosePanelProps {
  /** 呼叫端用 key={page.id}。 */
  page: Page;
  /** 本書已設定角色；false 時停用並連到角色工作室。 */
  hasCharacter: boolean;
  characterHref: string;
  /** POST /pages/{id}/poses {action}（一次一張）；失敗時呼叫端顯示 toast，promise 照常 resolve。 */
  onGenerate: (action: string) => Promise<unknown>;
  /** POST 進行中。 */
  generating: boolean;
  /** 本頁角色動作排隊中＋生成中的張數。 */
  activeCount: number;
}

/**
 * 角色模式的面板。
 * Task 8 佔位。Task 10 換成完整版：動作描述（1–300 字）、十個常用動作按鈕、「產生角色動作」（整段停用防連點）。
 */
export function PosePanel(props: PosePanelProps) {
  return (
    <section aria-label="角色動作設定" className={styles.panel} data-page={props.page.id}>
      <h2 className={styles.h2}>角色動作</h2>
    </section>
  );
}
