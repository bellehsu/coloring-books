import type { ReactNode } from "react";
import Link from "next/link";
import type { Book } from "@/api/client";
import { AppBar } from "@/components/shell/AppBar";
import { BookBar } from "@/components/shell/BookBar";
import { BookSettingsDialog } from "@/features/books/BookSettingsDialog";
import { ExportDrawer } from "./ExportDrawer";
import styles from "./EditorPage.module.css";

/**
 * 編輯器的外框：AppBar、BookBar（匯出 PDF、繪本設定）、還沒有角色的提醒，以及匯出抽屜、繪本設定與
 * 「清掉補過的線？」對話框（lossDialog）。開關狀態由 EditorPage 管。
 */
export function EditorShell({
  book,
  hasCharacter,
  exportOpen,
  onExportOpenChange,
  settingsOpen,
  onSettingsOpenChange,
  beforeExport,
  lossDialog,
  children,
}: {
  book: Book;
  hasCharacter: boolean;
  exportOpen: boolean;
  onExportOpenChange: (open: boolean) => void;
  settingsOpen: boolean;
  onSettingsOpenChange: (open: boolean) => void;
  /** 匯出前要先做完的事（還在等的自動合成）。 */
  beforeExport: () => Promise<void>;
  lossDialog: ReactNode;
  children: ReactNode;
}) {
  return (
    <>
      <AppBar crumbs={[{ label: "我的繪本", href: "/" }, { label: book.title, href: `/books/${book.id}` }]} />
      <BookBar book={book} onExport={() => onExportOpenChange(true)} onSettings={() => onSettingsOpenChange(true)} />
      {!hasCharacter && (
        <p className={styles.notice}>
          這本繪本還沒設定角色，不能產生角色動作。<Link href={`/books/${book.id}/setup`}>前往角色工作室</Link>
        </p>
      )}
      {children}
      <ExportDrawer open={exportOpen} onClose={() => onExportOpenChange(false)} book={book} beforeExport={beforeExport} />
      <BookSettingsDialog open={settingsOpen} onClose={() => onSettingsOpenChange(false)} book={book} />
      {lossDialog}
    </>
  );
}
