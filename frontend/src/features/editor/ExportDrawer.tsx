import type { Book } from "@/api/client";

/**
 * 匯出 PDF 抽屜。Task 11 會取代這個佔位元件（目前不顯示任何東西）。
 * EditorPage 以 BookBar 的「匯出」「匯出 PDF」開啟。
 */
export interface ExportDrawerProps {
  open: boolean;
  onClose: () => void;
  book: Book;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function ExportDrawer(_props: ExportDrawerProps) {
  return null;
}
