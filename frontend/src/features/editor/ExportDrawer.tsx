"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Book } from "@/api/client";
import { qk, useExportSummary, usePages } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { Icons } from "@/components/ui/Icons";
import {
  PAPER_LABELS,
  PRINT_MODE_LABELS,
  computeLayout,
  formatCm,
} from "@/lib/layout";
import styles from "./ExportDrawer.module.css";

const Download = Icons.download;

type Include = "passed" | "all";

export interface ExportDrawerProps {
  open: boolean;
  onClose: () => void;
  book: Book;
}

export function ExportDrawer({ open, onClose, book }: ExportDrawerProps) {
  const qc = useQueryClient();
  const summary = useExportSummary(book.id, open);
  const pages = usePages(book.id);
  const [include, setInclude] = useState<Include>("passed");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(
    null,
  );

  // 摘要端點可能重算線稿並把頁面退回草稿：每次載入新摘要後重抓頁面與書。
  const seen = useRef(0);
  const updatedAt = summary.dataUpdatedAt;
  useEffect(() => {
    if (!open || !updatedAt || updatedAt === seen.current) return;
    seen.current = updatedAt;
    void qc.invalidateQueries({ queryKey: qk.pages(book.id) });
    void qc.invalidateQueries({ queryKey: qk.book(book.id) });
  }, [open, updatedAt, qc, book.id]);

  const layout = computeLayout(book.paper_size, book.print_mode);
  const sentence =
    book.print_mode === "home"
      ? `${PAPER_LABELS[book.paper_size]} ${PRINT_MODE_LABELS.home}版面：每頁 ${formatCm(layout.contentMm)}（四邊留 10 mm 白邊），1-bit、300 DPI。`
      : `${PAPER_LABELS[book.paper_size]} ${PRINT_MODE_LABELS.print_shop}版面：每頁 ${formatCm(layout.contentMm)}（含 3 mm 出血），1-bit、300 DPI。`;

  const data = summary.data;
  const passed = data?.passed ?? 0;
  const skipped = data?.skipped ?? [];
  const selectedCount = data
    ? data.total - skipped.filter((s) => s.reason === "no_selection").length
    : 0;
  const count = include === "passed" ? passed : selectedCount;
  const needsFix = new Set(
    (pages.data ?? []).filter((p) => p.needs_fix).map((p) => p.id),
  );

  async function download() {
    if (busy || count === 0) return;
    setBusy(true);
    setMsg(null);
    try {
      const { blob, filename } = await api.download(
        `/books/${book.id}/export.pdf?include=${include}`,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${book.title}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMsg({ tone: "ok", text: `已產生「${a.download}」` });
      // 匯出時後端可能重算並退回草稿
      void qc.invalidateQueries({ queryKey: qk.pages(book.id) });
      void qc.invalidateQueries({ queryKey: qk.exportSummary(book.id) });
    } catch (e) {
      setMsg({
        tone: "error",
        text: e instanceof ApiError ? e.message : "匯出失敗，請稍後再試",
      });
    } finally {
      setBusy(false);
    }
  }

  const reason = (r: "no_selection" | "not_passed", pageId: number) =>
    r === "no_selection"
      ? "還沒選圖"
      : needsFix.has(pageId)
        ? "需要修線"
        : "未通過 QA";

  let hint: string;
  if (summary.isLoading) hint = "讀取中…";
  else if (summary.isError) hint = "讀不到匯出資訊";
  else if (count === 0)
    hint =
      include === "passed"
        ? "沒有通過的頁面可以匯出，先讓頁面通過 QA。"
        : "沒有已選圖的頁面可以匯出。";
  else hint = "約 10 秒";

  return (
    <Drawer open={open} onClose={onClose} title="匯出 PDF">
      {summary.isError ? (
        <p role="alert" className={styles.error}>
          {summary.error instanceof ApiError
            ? summary.error.message
            : "讀不到匯出資訊，請稍後再試"}
        </p>
      ) : (
        <>
          <div className={styles.stats}>
            <div className={`${styles.stat} ${styles.ok}`}>
              <span>已通過</span>
              <b data-testid="passed-count">{data ? passed : "–"}</b>
            </div>
            <div className={`${styles.stat} ${styles.warn}`}>
              <span>未通過</span>
              <b data-testid="failed-count">
                {data ? data.total - passed : "–"}
              </b>
            </div>
          </div>
          <p className={styles.layout}>{sentence}</p>

          <fieldset className={styles.fieldset}>
            <legend className={styles.legend}>匯出範圍</legend>
            {(
              [
                ["passed", "只有通過的頁面", `${passed} 頁，可直接送印`],
                [
                  "all",
                  "所有已選圖的頁面",
                  `${selectedCount} 頁，含未通過，試印用`,
                ],
              ] as const
            ).map(([v, name, note]) => (
              <label
                key={v}
                className={`${styles.option} ${include === v ? styles.optionOn : ""}`}
              >
                <input
                  type="radio"
                  name="include"
                  checked={include === v}
                  onChange={() => setInclude(v)}
                />
                <span>
                  <b>{name}</b>
                  <br />
                  <span className={styles.note}>{data ? note : "…"}</span>
                </span>
              </label>
            ))}
          </fieldset>

          {skipped.length > 0 && (
            <div className={styles.skipped}>
              <b>不會匯出的頁面</b>
              <ul>
                {skipped.map((s) => (
                  <li key={s.page_id}>
                    <span className={styles.pageNo}>
                      第 {s.position + 1} 頁
                    </span>
                    <span
                      className={
                        s.reason === "not_passed" ? styles.warnText : undefined
                      }
                    >
                      {reason(s.reason, s.page_id)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className={styles.foot}>
        <Button
          variant="primary"
          size="lg"
          icon={<Download size={18} />}
          disabled={busy || !data || count === 0}
          onClick={download}
        >
          {busy ? "產生中…" : `下載 PDF（${count} 頁）`}
        </Button>
        {msg ? (
          <span
            role={msg.tone === "error" ? "alert" : "status"}
            className={msg.tone === "error" ? styles.errorText : styles.msg}
          >
            {msg.text}
          </span>
        ) : (
          <span className={styles.msg}>{hint}</span>
        )}
      </div>
    </Drawer>
  );
}
