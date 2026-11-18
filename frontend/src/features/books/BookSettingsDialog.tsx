"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError, type Book } from "@/api/client";
import { useUpdateBook } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { whenBookWritesIdle } from "@/features/editing/pendingWrites";
import {
  PAPER_LABELS,
  PAPER_MM,
  PAPER_SIZES,
  PRINT_MODE_LABELS,
  computeLayout,
  formatCm,
  ratioGroup,
  type PaperSize,
  type PrintMode,
} from "@/lib/layout";
import styles from "./BookSettingsDialog.module.css";

const MODE_HINT: Record<PrintMode, string> = {
  home: "四邊留 10 mm 白邊，印表機印不到邊緣也不會切到",
  print_shop: "滿版加 3 mm 出血，裁切線內留 5 mm 安全區",
};
const SAME_RATIO = "只能改成同比例的尺寸";

/** 表單放在子元件：Dialog 關閉時卸載，下次開啟就是目前書本的值。 */
export function BookSettingsDialog({
  open,
  onClose,
  book,
}: {
  open: boolean;
  onClose: () => void;
  book: Book;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="繪本設定">
      <SettingsForm onClose={onClose} book={book} />
    </Dialog>
  );
}

function SettingsForm({ onClose, book }: { onClose: () => void; book: Book }) {
  const qc = useQueryClient();
  const update = useUpdateBook(book.id);
  /** 改版面前等待中的修線寫入（送完才 PATCH），與等待失敗的原因。 */
  const [waiting, setWaiting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [title, setTitle] = useState(book.title);
  const [size, setSize] = useState<PaperSize>(book.paper_size);
  const [mode, setMode] = useState<PrintMode>(book.print_mode);
  const trimmed = title.trim();
  const layoutChanged = size !== book.paper_size || mode !== book.print_mode;
  const dirty = layoutChanged || (trimmed !== book.title && trimmed !== "");
  const layout = computeLayout(size, mode);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!dirty || !trimmed || update.isPending || waiting) return;
    if (layoutChanged) {
      // 伺服器改版面時會換算已存的修線座標（C1）：還在送或送失敗的修線要先處理完，否則舊座標會蓋回去
      setWriteError(null);
      setWaiting(true);
      try {
        await whenBookWritesIdle(qc, book.id);
      } catch (err) {
        setWriteError(`修線還沒存好（${err instanceof Error ? err.message : "儲存失敗"}），請先重送或重新整理後再改紙張`);
        return;
      } finally {
        setWaiting(false);
      }
    }
    const body: {
      title?: string;
      paper_size?: PaperSize;
      print_mode?: PrintMode;
    } = {};
    if (trimmed !== book.title) body.title = trimmed;
    if (size !== book.paper_size) body.paper_size = size;
    if (mode !== book.print_mode) body.print_mode = mode;
    update.mutate(body, { onSuccess: onClose });
  }

  const error = update.error;
  return (
    <form className={styles.form} onSubmit={submit}>
      <label className={styles.field}>
        書名
        <input
          type="text"
          className={styles.input}
          value={title}
          onChange={(e) => {
            update.reset();
            setTitle(e.target.value);
          }}
          maxLength={100}
          autoComplete="off"
        />
      </label>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>紙張尺寸</legend>
        <div role="radiogroup" aria-label="紙張尺寸" className={styles.sizes}>
          {PAPER_SIZES.map((s) => {
            const [w, h] = PAPER_MM[s];
            const k = 30 / 29.7;
            const on = s === size;
            const locked = ratioGroup(s) !== ratioGroup(book.paper_size);
            return (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={on}
                disabled={locked}
                title={locked ? SAME_RATIO : undefined}
                className={`${styles.card} ${styles.sizeCard} ${on ? styles.on : ""} ${locked ? styles.locked : ""}`}
                onClick={() => {
                  update.reset();
                  setSize(s);
                }}
              >
                <span className={styles.shapeBox}>
                  <span
                    className={`${styles.shape} ${on ? styles.shapeOn : ""}`}
                    style={{
                      width: Math.round((w / 10) * k),
                      height: Math.round((h / 10) * k),
                    }}
                  />
                </span>
                <b className={styles.sizeName}>{PAPER_LABELS[s]}</b>
                <span className={styles.dim}>{formatCm([w, h])}</span>
                {locked && <span className={styles.lockNote}>{SAME_RATIO}</span>}
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>列印方式</legend>
        <div role="radiogroup" aria-label="列印方式" className={styles.modes}>
          {(["home", "print_shop"] as const).map((m) => {
            const on = m === mode;
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={on}
                className={`${styles.card} ${styles.modeCard} ${on ? styles.on : ""}`}
                onClick={() => {
                  update.reset();
                  setMode(m);
                }}
              >
                {m === "home" ? (
                  <span className={styles.homeIcon} aria-hidden="true">
                    <span className={styles.homeInner} />
                  </span>
                ) : (
                  <span className={styles.shopIcon} aria-hidden="true">
                    <span className={styles.shopInner} />
                  </span>
                )}
                <span className={styles.modeText}>
                  <b className={styles.modeName}>{PRINT_MODE_LABELS[m]}</b>
                  <span className={styles.dim}>{MODE_HINT[m]}</span>
                </span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <div className={styles.summary}>
        <span>
          內容範圍 <b>{formatCm(layout.contentMm)}</b>
          {mode === "print_shop" && "（含出血）"}
        </span>
      </div>

      {layoutChanged && (
        <p role="status" className={styles.warning}>
          線稿會重新計算，有缺口的頁面會退回草稿，記得再跑一次自動補線。
        </p>
      )}

      {writeError && (
        <p role="alert" className={styles.error}>
          {writeError}
        </p>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error instanceof ApiError ? error.message : "儲存失敗，請稍後再試"}
        </p>
      )}

      <div className={styles.actions}>
        <Button onClick={onClose}>取消</Button>
        <Button
          type="submit"
          variant="primary"
          disabled={!dirty || update.isPending || waiting}
        >
          儲存
        </Button>
      </div>
    </form>
  );
}
