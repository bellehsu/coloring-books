"use client";

import Link from "next/link";
import { ApiError } from "@/api/client";
import { useSharedBook } from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { Icons } from "@/components/ui/Icons";
import styles from "./SharedBookView.module.css";

const Download = Icons.download;

/** 別人分享的書的唯讀檢視：標題、作者、完成的頁面（大圖）與下載 PDF。沒有任何編輯介面。 */
export function SharedBookView({ bookId }: { bookId: number }) {
  const q = useSharedBook(bookId);
  const book = q.data;
  const gone = q.error instanceof ApiError && q.error.status === 404;

  return (
    <>
      <AppBar />
      <main className={styles.main}>
        {q.isPending && <p className={styles.note}>載入中…</p>}
        {gone && (
          <div className={styles.gone}>
            <h1 className={styles.h1}>這本繪本已經不再分享</h1>
            <Link href="/" className={styles.home}>
              回首頁
            </Link>
          </div>
        )}
        {q.isError && !gone && (
          <p role="alert" className={styles.error}>
            {q.error instanceof ApiError ? q.error.message : "無法載入繪本"}
          </p>
        )}
        {book && (
          <>
            <div className={styles.head}>
              <div className={styles.titles}>
                <h1 className={styles.h1}>{book.title}</h1>
                <span className={styles.author}>作者：{book.author}</span>
              </div>
              <a href={`/api/shared/books/${book.id}/export.pdf`} download className={styles.download}>
                <Download size={18} />
                下載 PDF
              </a>
            </div>
            <div className={styles.pages}>
              {book.pages.map((p, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={p.id}
                  src={`/api/shared/pages/${p.id}/image.png`}
                  alt={`第 ${i + 1} 頁`}
                  className={styles.page}
                  loading="lazy"
                />
              ))}
            </div>
          </>
        )}
      </main>
    </>
  );
}
