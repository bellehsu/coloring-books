"use client";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { THEME_PACKS, type ThemePack } from "@/lib/themePacks";
import styles from "./ThemePackDialog.module.css";

export interface ThemePackDialogProps {
  open: boolean;
  onClose: () => void;
  onChoose: (pack: ThemePack) => void;
}

/** 套用主題包：列出內建主題包；選一個後交給「貼上整本故事」預覽，確認前不會寫入任何資料。 */
export function ThemePackDialog({ open, onClose, onChoose }: ThemePackDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} title="套用主題包">
      <div className={styles.body}>
        <p className={styles.hint}>選一個主題包，下一步會先預覽，確認後才會填入。已有內容的欄位不會被覆蓋。</p>
        <ul className={styles.list}>
          {THEME_PACKS.map((p) => (
            <li key={p.id}>
              <button type="button" className={styles.pack} onClick={() => onChoose(p)}>
                <strong>{p.title}</strong>
                <span>{p.description}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className={styles.row}>
          <Button onClick={onClose}>取消</Button>
        </div>
      </div>
    </Dialog>
  );
}
