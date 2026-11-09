"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/api/client";
import { useCreateBook } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icons } from "@/components/ui/Icons";
import {
  PAPER_LABELS,
  PAPER_MM,
  PAPER_SIZES,
  PRINT_MODE_LABELS,
  computeLayout,
  formatCm,
  type PaperSize,
  type PrintMode,
} from "@/lib/layout";
import styles from "./CreateBookDialog.module.css";

const ArrowRight = Icons["arrow-right"];

const MODE_HINT: Record<PrintMode, string> = {
  home: "四邊留 10 mm 白邊，印表機印不到邊緣也不會切到",
  print_shop: "滿版加 3 mm 出血，裁切線內留 5 mm 安全區",
};

export function CreateBookDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const create = useCreateBook();
  const [title, setTitle] = useState("");
  const [size, setSize] = useState<PaperSize>("a4");
  const [mode, setMode] = useState<PrintMode>("print_shop");
  const layout = computeLayout(size, mode);
  const trimmed = title.trim();

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!trimmed || create.isPending) return;
    create.mutate(
      { title: trimmed, paper_size: size, print_mode: mode },
      {
        onSuccess: (book) => {
          onClose();
          router.push(`/books/${book.id}/character`);
        },
      },
    );
  }

  const error = create.error;
  return (
    <Dialog open={open} onClose={onClose} title="建立新繪本">
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.field}>
          書名
          <input
            type="text"
            className={styles.input}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
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
              return (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={`${styles.card} ${styles.sizeCard} ${on ? styles.on : ""}`}
                  onClick={() => setSize(s)}
                >
                  <span className={styles.shapeBox}>
                    <span
                      className={`${styles.shape} ${on ? styles.shapeOn : ""}`}
                      style={{ width: Math.round((w / 10) * k), height: Math.round((h / 10) * k) }}
                    />
                  </span>
                  <b className={styles.sizeName}>{PAPER_LABELS[s]}</b>
                  <span className={styles.dim}>{formatCm([w, h])}</span>
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
                  onClick={() => setMode(m)}
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
          <span>
            生成尺寸 <b>{`${layout.generationSize[0]} × ${layout.generationSize[1]}`}</b>
          </span>
          <span>之後只能改成同比例的尺寸</span>
        </div>

        {error && (
          <p role="alert" className={styles.error}>
            {error instanceof ApiError ? error.message : "建立失敗，請稍後再試"}
          </p>
        )}

        <div className={styles.actions}>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={!trimmed || create.isPending}>
            建立，接著設定角色
            <ArrowRight size={18} />
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
