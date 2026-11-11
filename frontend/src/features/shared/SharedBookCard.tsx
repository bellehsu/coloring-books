import Link from "next/link";
import type { SharedBook } from "@/api/client";
import styles from "./SharedBookCard.module.css";

/** 「大家的繪本」的一張卡：封面縮圖、書名、作者、頁數；連到唯讀檢視。 */
export function SharedBookCard({ book }: { book: SharedBook }) {
  return (
    <Link href={`/shared/${book.id}`} className={styles.card}>
      <div className={styles.stage}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={book.cover_image_url} alt="" className={styles.cover} loading="lazy" />
      </div>
      <div className={styles.body}>
        <b className={styles.title}>{book.title}</b>
        <span className={styles.meta}>作者：{book.author}</span>
        <span className={styles.meta}>{book.page_count} 頁</span>
      </div>
    </Link>
  );
}
