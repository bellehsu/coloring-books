"use client";

import { useState } from "react";
import { AppBar } from "@/components/shell/AppBar";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import { ApiError } from "@/api/client";
import { useBooks } from "@/api/hooks";
import { BookCard } from "@/features/books/BookCard";
import { CreateBookDialog } from "@/features/books/CreateBookDialog";
import styles from "./page.module.css";

export default function Home() {
  const books = useBooks();
  const [creating, setCreating] = useState(false);
  const list = books.data;

  return (
    <>
      <AppBar />
      <main className={styles.main}>
        <div className={styles.head}>
          <div className={styles.titles}>
            <h1 className={styles.h1}>我的繪本</h1>
            {list && <span className={styles.sub}>{list.length} 本</span>}
          </div>
          <Button variant="primary" icon={<Icons.plus size={18} />} onClick={() => setCreating(true)} className={styles.create}>
            建立新繪本
          </Button>
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
            <button type="button" className={styles.newCard} onClick={() => setCreating(true)}>
              <span className={styles.plus}>
                <Icons.plus size={22} />
              </span>
              建立新繪本
            </button>
          </div>
        )}
      </main>
      {creating && <CreateBookDialog open onClose={() => setCreating(false)} />}
    </>
  );
}
