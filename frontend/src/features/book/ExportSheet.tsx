"use client";

import { useState } from "react";
import type { Book, OverviewPage } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { ExportOptions } from "@/features/editor/ExportDrawer";
import styles from "./ExportSheet.module.css";

export interface ExportSheetProps {
  onClose: () => void;
  book: Book;
  pages: OverviewPage[];
}

const thumbOf = (p: OverviewPage) => p.lineart_url ?? p.thumb_url;

/**
 * 「印出這本書」：先看一眼（可以印的頁面縮圖；請看一下的頁也列出來，可以勾選一起印），
 * 再到原本的 PDF 選項。開啟時才掛載，每次都從「看一眼」開始。
 */
export function ExportSheet({ onClose, book, pages }: ExportSheetProps) {
  const [step, setStep] = useState<"glance" | "options">("glance");
  const [withAttention, setWithAttention] = useState(false);
  const ready = pages.filter((p) => p.state === "ready");
  const attention = pages.filter((p) => p.state === "attention");
  const unfinished = pages.length - ready.length - attention.length;
  const [cw, ch] = book.layout.content_px;

  return (
    <Drawer open onClose={onClose} title="印出這本書">
      {step === "glance" ? (
        <>
          <div className={styles.intro}>
            <h3 className={styles.h3}>先看一眼</h3>
            <p className={styles.note}>
              {ready.length > 0 ? `這 ${ready.length} 頁會印出來。` : "還沒有可以印的頁面。"}
              {unfinished > 0 && `還有 ${unfinished} 頁還沒做完，不會印出來。`}
            </p>
          </div>
          {ready.length > 0 && (
            <ul aria-label="會印出來的頁面" className={styles.grid}>
              {ready.map((p) => {
                const src = thumbOf(p);
                return (
                  <li key={p.id} className={styles.thumb}>
                    <div className={styles.paper} style={{ aspectRatio: `${cw} / ${ch}` }}>
                      {src && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={src} alt={`第 ${p.position + 1} 頁`} loading="lazy" className={styles.img} />
                      )}
                    </div>
                    <span className={styles.no}>{p.position + 1}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {attention.length > 0 && (
            <div className={styles.attention}>
              <b className={styles.attentionTitle}>請看一下的頁面</b>
              <ul aria-label="請看一下的頁面" className={styles.attentionList}>
                {attention.map((p) => (
                  <li key={p.id}>
                    <b>第 {p.position + 1} 頁</b>
                    {p.attention_detail && <span className={styles.detail}>{p.attention_detail}</span>}
                  </li>
                ))}
              </ul>
              <label className={styles.include}>
                <input type="checkbox" checked={withAttention} onChange={(e) => setWithAttention(e.target.checked)} />
                這些頁也一起印（試印）
              </label>
            </div>
          )}
          <div className={styles.foot}>
            <Button variant="primary" size="lg" onClick={() => setStep("options")}>
              看好了，下一步 →
            </Button>
          </div>
        </>
      ) : (
        <>
          <Button variant="ghost" className={styles.back} onClick={() => setStep("glance")}>
            ← 回去看一眼
          </Button>
          <ExportOptions
            book={book}
            active
            initialInclude={withAttention ? "all" : "passed"}
            emptyPassedHint="還沒有可以印的頁面。"
          />
        </>
      )}
    </Drawer>
  );
}
