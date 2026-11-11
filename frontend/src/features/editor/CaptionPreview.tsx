import type { Page } from "@/api/client";
import { CAPTION_BLANK, printedCaptions } from "@/features/fix/fixState";
import styles from "./CaptionPreview.module.css";

/**
 * 舞台下方的圖下方文字預覽（構圖與補線都顯示）與「預覽列印頁」連結（開新分頁看後端畫好的 print.png）。
 * 只顯示會印的行（C 的中文／英文開關）；兩個都關時說圖下方留白。沒有要印的字時整塊不出現。
 * 文字是伺服器上已存的值。
 */
export function CaptionPreview({ page }: { page: Page }) {
  const printed = printedCaptions(page);
  const blank = printed === "blank";
  const zh = blank ? "" : printed.zh;
  const en = blank ? "" : printed.en;
  if (!blank && !zh && !en) return null;
  return (
    <div role="group" aria-label="圖下方的文字預覽" className={styles.box}>
      {blank && <p className={styles.en}>{CAPTION_BLANK}</p>}
      {zh && <p className={styles.zh}>{zh}</p>}
      {en && <p className={styles.en}>{en}</p>}
      {page.selected_generation_id != null && (
        <a href={`/api/pages/${page.id}/print.png`} target="_blank" rel="noopener noreferrer" className={styles.link}>
          預覽列印頁
        </a>
      )}
    </div>
  );
}
