import type { Page } from "@/api/client";
import styles from "./CaptionPreview.module.css";

/**
 * 舞台下方的圖下方文字預覽（構圖與補線都顯示）與「預覽列印頁」連結（開新分頁看後端畫好的 print.png）。
 * 兩行都沒有文字時整塊不出現。文字是伺服器上已存的值。
 */
export function CaptionPreview({ page }: { page: Page }) {
  const zh = (page.caption_zh ?? "").trim();
  const en = (page.caption_en ?? "").trim();
  if (!zh && !en) return null;
  return (
    <div role="group" aria-label="圖下方的文字預覽" className={styles.box}>
      {zh && <p className={styles.zh}>{zh}</p>}
      {en && <p className={styles.en}>{en}</p>}
      <a href={`/api/pages/${page.id}/print.png`} target="_blank" rel="noopener noreferrer" className={styles.link}>
        預覽列印頁
      </a>
    </div>
  );
}
