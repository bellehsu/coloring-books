"use client";

import { useEffect, useRef, useState } from "react";
import type { Book } from "@/api/client";
import { useDeleteBook } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import styles from "./BookCardMenu.module.css";

/** 書架卡片右上角的「⋯」選單：目前只有「刪除這本書」（只有自己的書才有）。 */
export function BookCardMenu({ book }: { book: Book }) {
  const toast = useToast();
  const del = useDeleteBook();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!book.mine) return null;

  async function remove() {
    try {
      await del.mutateAsync(book);
      toast.show(`已刪除「${book.title}」`, "success");
    } catch (e) {
      toast.show(errMsg(e, "沒辦法刪除這本書，請稍後再試"), "error");
    } finally {
      setConfirm(false);
    }
  }

  return (
    <div ref={root} className={styles.root}>
      <button
        ref={trigger}
        type="button"
        className={styles.trigger}
        aria-label={`「${book.title}」的選單`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && (
        <div role="menu" className={styles.menu}>
          <button
            type="button"
            role="menuitem"
            className={styles.item}
            autoFocus
            onClick={() => {
              setOpen(false);
              setConfirm(true);
            }}
          >
            刪除這本書
          </button>
        </div>
      )}
      <Dialog open={confirm} onClose={() => setConfirm(false)} title={`確定要刪除「${book.title}」嗎？`} closeDisabled={del.isPending}>
        <div className={styles.confirm}>
          <p>{book.stats.pages} 頁、所有圖和修過的線都會刪掉，刪了就救不回來。分享出去的連結也會失效。</p>
          <div className={styles.actions}>
            <Button onClick={() => setConfirm(false)} disabled={del.isPending}>
              取消
            </Button>
            <Button variant="danger" onClick={() => void remove()} disabled={del.isPending}>
              刪除
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
