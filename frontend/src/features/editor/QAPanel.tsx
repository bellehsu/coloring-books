import type { Book, Generation, Page, QA } from "@/api/client";
import styles from "./EditorPage.module.css";

/**
 * QA 模式的檢查面板（自動檢查、角色外型確認、通過／退回、下載本頁 PNG）。
 * Task 10 會取代這個佔位元件。注意：自動補線或通過前要先
 * `await editsQueueFor(qc, book.id, page.id, generation.id).flush()`，確保修線已寫入。
 */
export interface QAPanelProps {
  book: Book;
  page: Page;
  qa: QA | undefined;
  generation: Generation | undefined;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function QAPanel(_props: QAPanelProps) {
  return (
    <section data-testid="qa-panel" className={styles.placeholder}>
      QA 檢查面板準備中
    </section>
  );
}
