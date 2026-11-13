"use client";

import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import styles from "./RedrawBar.module.css";

export type RedrawCount = 1 | 2 | 3;

const COUNTS: { value: `${RedrawCount}`; label: string }[] = [
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "3", label: "3" },
];

export interface RedrawBarProps {
  /** 勾了幾頁。 */
  selected: number;
  /** 每頁畫幾張（第 1 張換上去，其餘是候選）。 */
  count: RedrawCount;
  onCount: (c: RedrawCount) => void;
  onSubmit: () => void;
  onCancel: () => void;
  busy: boolean;
}

/** B「選取多頁」底部的重畫列（spec §4.4 批次、mockups/2026-11-13/A_overview_select.png）。 */
export function RedrawBar({ selected, count, onCount, onSubmit, onCancel, busy }: RedrawBarProps) {
  return (
    <section aria-label="重畫選取的頁" className={styles.bar}>
      <div className={styles.what}>
        <b className={styles.selected}>已選 {selected} 頁</b>
        <span className={styles.sub}>每頁重畫主角動作，背景和字不動</span>
      </div>
      <div className={styles.count}>
        <span>每頁畫</span>
        <Segmented
          kind="radio"
          label="每頁畫幾張"
          options={COUNTS}
          value={`${count}`}
          onChange={(v) => onCount(Number(v) as RedrawCount)}
        />
        <span>張讓我挑</span>
        <span className={styles.total}>會畫 {selected * count} 張</span>
      </div>
      <div className={styles.buttons}>
        <Button variant="primary" disabled={selected === 0 || busy} aria-busy={busy} onClick={onSubmit}>
          重畫 {selected} 頁的主角
        </Button>
        <Button onClick={onCancel}>取消</Button>
      </div>
    </section>
  );
}
