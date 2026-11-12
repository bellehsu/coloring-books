"use client";

import { useState } from "react";
import type { Book, OverviewPage } from "@/api/client";
import { useExportSummary } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { ExportOptions } from "@/features/editing/ExportOptions";
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
 *
 * 「會印出來」與 PDF 用同一份名單：匯出摘要的 ready_ids（通過而且沒有請看一下，等於 include=ready）。
 * 換一張失敗、還是 PASSED 的「請看一下」頁也不算（spec §4.3：列出來，勾了才一起印＝include=all）。
 * 摘要還沒回來時先用總覽的「可以印」。
 */
export function ExportSheet({ onClose, book, pages }: ExportSheetProps) {
  const [step, setStep] = useState<"glance" | "options">("glance");
  const [withAttention, setWithAttention] = useState(false);
  const summary = useExportSummary(book.id).data;
  const readyIds = summary ? new Set(summary.ready_ids) : null;
  const ready = pages.filter((p) => (readyIds ? readyIds.has(p.id) : p.state === "ready"));
  const attention = pages.filter((p) => p.state === "attention" && !readyIds?.has(p.id));
  const unfinished = pages.length - ready.length - attention.length;
  // 勾了一起印（include=all）：有圖的頁都印，只有還沒有圖的不印
  const noImage = summary
    ? summary.skipped.filter((s) => s.reason === "no_selection").length
    : pages.filter((p) => p.thumb_url == null).length;
  const allCount = pages.length - noImage;
  const [cw, ch] = book.layout.content_px;

  return (
    <Drawer open onClose={onClose} title="印出這本書">
      {step === "glance" ? (
        <>
          <div className={styles.intro}>
            <h3 className={styles.h3}>先看一眼</h3>
            <p className={styles.note}>
              {ready.length > 0 ? `這 ${ready.length} 頁會印出來。` : "還沒有可以印的頁面。"}
              {withAttention
                ? `加上請看一下的頁，一共印 ${allCount} 頁。${noImage > 0 ? `還有 ${noImage} 頁還沒有圖，不會印出來。` : ""}`
                : unfinished > 0 && `還有 ${unfinished} 頁還沒做完，不會印出來。`}
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
            variant="book"
            initialInclude={withAttention ? "all" : "ready"}
            emptyPassedHint="還沒有可以印的頁面。"
          />
        </>
      )}
    </Drawer>
  );
}
