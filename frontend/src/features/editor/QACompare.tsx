import type { Book, Generation, Page, QA } from "@/api/client";
import styles from "./QA.module.css";

export interface QACompareProps {
  book: Book;
  page: Page;
  /** 本頁 QA；載入中為 undefined。 */
  qa: QA | undefined;
  /** 目前選定的 generation（彩圖）；找不到為 undefined。 */
  generation: Generation | undefined;
}

/** `?v=` 的值：先看 qa.lineart_url，再看 page.lineart_url。 */
export function overlayKey(qa: QA | undefined, page: Page): string | null {
  for (const u of [qa?.lineart_url, page.lineart_url]) {
    if (!u) continue;
    const m = u.match(/[?&]v=([^&]+)/);
    if (m) return m[1];
  }
  return null;
}

/** QA 模式的舞台：角色參考／本頁彩圖／線稿＋缺口紅圈＋裁切線與安全區。 */
export function QACompare({ book, page, qa, generation }: QACompareProps) {
  const key = overlayKey(qa, page);
  const refId = book.character_generation_id;
  return (
    <div data-testid="qa-compare" className={styles.compare}>
      <figure className={styles.fig}>
        <div className={styles.frame} style={{ aspectRatio: "832 / 1216" }}>
          {refId != null ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={`/api/generations/${refId}/image`} alt="本書角色參考圖" className={styles.img} />
          ) : (
            <span className={styles.empty}>尚未設定角色</span>
          )}
        </div>
        <figcaption className={styles.cap}>本書角色（參考）</figcaption>
      </figure>
      <figure className={styles.fig}>
        <div className={styles.frame} style={{ aspectRatio: "864 / 1216" }}>
          {generation?.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={generation.image_url} alt="本頁彩圖" className={styles.img} />
          ) : (
            <span className={styles.empty}>沒有彩圖</span>
          )}
        </div>
        <figcaption className={styles.cap}>本頁彩圖</figcaption>
      </figure>
      <figure className={styles.fig}>
        <div className={styles.frame} style={{ aspectRatio: "2551 / 3579" }}>
          {key ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`/api/pages/${page.id}/qa/overlay.png?v=${key}`}
              alt="線稿，紅圈標出缺口，含裁切線與安全區"
              className={styles.img}
            />
          ) : (
            <span className={styles.empty}>線稿計算中…</span>
          )}
        </div>
        <figcaption className={styles.cap}>線稿・缺口・裁切線與安全區</figcaption>
      </figure>
    </div>
  );
}
