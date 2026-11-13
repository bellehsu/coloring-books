"use client";

import { AppBar } from "@/components/shell/AppBar";
import buttonStyles from "@/components/ui/Button.module.css";
import { Icons } from "@/components/ui/Icons";
import { ApiError } from "@/api/client";
import Link from "next/link";
import { useBooks, useMe, useSharedBooks } from "@/api/hooks";
import { BookCard } from "@/features/books/BookCard";
import { SharedBookCard } from "@/features/shared/SharedBookCard";
import styles from "./page.module.css";

export default function Home() {
  const books = useBooks();
  const shared = useSharedBooks();
  const me = useMe();
  const list = books.data;
  const others = shared.data;

  return (
    <>
      <AppBar />
      <main className={styles.main}>
        <div className={styles.head}>
          <div className={styles.titles}>
            <h1 className={styles.h1} tabIndex={-1}>我的繪本</h1>
            {list && <span className={styles.sub}>{list.length} 本</span>}
          </div>
          {me.data?.is_admin && !me.data.via_dev_owner && (
            <Link href="/admin/invites" className={styles.adminLink}>
              邀請管理
            </Link>
          )}
          {/* A 做一本新的著色書（spec §4.5） */}
          <Link href="/books/new" className={`${buttonStyles.btn} ${buttonStyles.primary} ${buttonStyles.md} ${styles.create}`}>
            <Icons.plus size={18} />
            建立新繪本
          </Link>
        </div>

        {books.isPending && (
          <div className={styles.grid} aria-busy="true" aria-label="載入中">
            {[0, 1, 2].map((i) => (
              <div key={i} className={styles.skeleton} />
            ))}
          </div>
        )}
        {books.isError && (
          <p role="alert" className={styles.error}>
            {books.error instanceof ApiError ? books.error.message : "無法載入繪本"}
          </p>
        )}
        {list && list.length === 0 && (
          <div className={styles.empty}>
            <p className={styles.emptyText}>還沒有繪本，建立第一本開始吧。</p>
          </div>
        )}
        {list && list.length > 0 && (
          <div className={styles.grid}>
            {list.map((b) => (
              <BookCard key={b.id} book={b} />
            ))}
            <Link href="/books/new" className={styles.newCard}>
              <span className={styles.plus}>
                <Icons.plus size={22} />
              </span>
              建立新繪本
            </Link>
          </div>
        )}

        <section aria-labelledby="shared-h" className={styles.section}>
          <div className={styles.titles}>
            <h2 id="shared-h" className={styles.h2}>
              大家的繪本
            </h2>
          </div>
          {shared.isError && (
            <p role="alert" className={styles.error}>
              {shared.error instanceof ApiError ? shared.error.message : "無法載入大家的繪本"}
            </p>
          )}
          {others && others.length === 0 && (
            <div className={styles.empty}>
              <p className={styles.emptyText}>還沒有人分享繪本。</p>
            </div>
          )}
          {others && others.length > 0 && (
            <div className={styles.grid}>
              {others.map((b) => (
                <SharedBookCard key={b.id} book={b} />
              ))}
            </div>
          )}
        </section>
      </main>
    </>
  );
}
