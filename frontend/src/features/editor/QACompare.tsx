import type { Book, Generation, Page, QA } from "@/api/client";
import styles from "./EditorPage.module.css";

/**
 * QA 模式的舞台（角色參考／本頁彩圖／線稿＋缺口＋裁切線與安全區）。
 * Task 10 會取代這個佔位元件；props 由 EditorPage 傳入，Task 10 可增減。
 */
export interface QACompareProps {
  book: Book;
  page: Page;
  /** 本頁 QA；載入中為 undefined。 */
  qa: QA | undefined;
  /** 目前選定的 generation（彩圖）；找不到為 undefined。 */
  generation: Generation | undefined;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function QACompare(_props: QACompareProps) {
  return (
    <div data-testid="qa-compare" className={styles.placeholder}>
      QA 比對畫面準備中
    </div>
  );
}
