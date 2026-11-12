"use client";

import type { Book } from "@/api/client";
import { Drawer } from "@/components/ui/Drawer";
import { ExportOptions } from "@/features/editing/ExportOptions";

export interface ExportDrawerProps {
  open: boolean;
  onClose: () => void;
  book: Book;
  /** 匯出前要先做完的事（編輯器：還在等的自動合成）。 */
  beforeExport?: () => Promise<void>;
}

/** 進階編輯的「匯出 PDF」：和 B 的「印出這本書」一樣的範圍與說法（預設只印可以印的頁，請看一下的頁不印）。 */
export function ExportDrawer({ open, onClose, book, beforeExport }: ExportDrawerProps) {
  return (
    <Drawer open={open} onClose={onClose} title="匯出 PDF">
      <ExportOptions book={book} active={open} beforeExport={beforeExport} initialInclude="ready" />
    </Drawer>
  );
}
