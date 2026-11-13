"use client";

import { useEffect, useRef } from "react";
import { Dialog } from "@/components/ui/Dialog";
import styles from "./SetupPage.module.css";

/** 自動複製不成功時的退路：把指令全選好，讓人自己按 Ctrl＋C（手機長按）。 */
export function CopyPromptDialog({ open, text, onClose }: { open: boolean; text: string; onClose: () => void }) {
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!open) return;
    const el = area.current;
    if (!el) return;
    el.focus();
    el.select();
  }, [open]);
  return (
    <Dialog open={open} onClose={onClose} title="複製給 AI 的指令">
      <div className={styles.confirm}>
        <textarea ref={area} readOnly aria-label="給 AI 的指令" className={`${styles.input} ${styles.promptArea}`} rows={10} value={text} />
        <p className={styles.hint}>已全選，按 Ctrl＋C（手機長按）複製</p>
      </div>
    </Dialog>
  );
}
