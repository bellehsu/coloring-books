"use client";

import type { Page } from "@/api/client";
import styles from "./Inspector.module.css";

export interface BackgroundPanelProps {
  /** 呼叫端用 key={page.id}：欄位只在掛載時初始化（sentFields 送出過的值優先，否則用 page）。 */
  page: Page;
  /** PATCH 本頁（scene_prompt／controlnet_strength）；失敗時呼叫端顯示 toast 並 reject。 */
  onPatch: (body: { scene_prompt?: string; controlnet_strength?: number }) => Promise<unknown>;
  /** 產生前的其他寫入（構圖草圖：立即 flush 並等它存好）。reject＝不要產生（呼叫端已顯示訊息）。 */
  beforeGenerate: () => Promise<unknown>;
  /** POST /pages/{id}/backgrounds（一次一張）；失敗時呼叫端顯示 toast，promise 照常 resolve。 */
  onGenerate: () => Promise<unknown>;
  /** POST 進行中。 */
  generating: boolean;
  /** 本頁背景排隊中＋生成中的張數。 */
  activeCount: number;
}

/**
 * 背景模式的面板。
 * Task 8 佔位（不在介面上產生東西）。Task 9 換成完整版：場景描述（sentFields 規則）、構圖強度 0–0.6、
 * 「產生背景」（先 flush 欄位與 beforeGenerate，再 onGenerate；整段停用防連點）、預估時間。
 */
export function BackgroundPanel(props: BackgroundPanelProps) {
  return (
    <section aria-label="背景設定" className={styles.panel} data-page={props.page.id}>
      <h2 className={styles.h2}>背景</h2>
    </section>
  );
}
