"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Book } from "@/api/client";
import { qk, useExportSummary, usePages, useShareBook } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import {
  PAPER_LABELS,
  PRINT_MODE_LABELS,
  computeLayout,
  formatCm,
} from "@/lib/layout";
import { errMsg } from "./errMsg";
import { fixHref } from "@/features/fix/fixState";
import { BookWritesError, retryFailedWrites, whenBookWritesIdle, type WriteFailure } from "./pendingWrites";
import styles from "./ExportOptions.module.css";

const Download = Icons.download;

/** ready：通過而且沒有「請看一下」；all：有選定圖的頁（試印）。 */
export type Include = "ready" | "all";

export interface ExportOptionsProps {
  book: Book;
  /** 顯示中（抽屜開著）才讀匯出摘要。 */
  active: boolean;
  /** 預選的匯出範圍。 */
  initialInclude?: Include;
  /** 匯出前要先做完的事（編輯器：還在等的自動合成）。 */
  beforeExport?: () => Promise<void>;
}

/** 匯出 PDF 的選項與下載（B 的「印出這本書」與進階編輯的「匯出 PDF」抽屜共用：可以印／請看一下／還沒做完的說法）。 */
export function ExportOptions({ book, active: open, initialInclude = "ready", beforeExport }: ExportOptionsProps) {
  const qc = useQueryClient();
  const summary = useExportSummary(book.id, open);
  const pages = usePages(book.id);
  const share = useShareBook(book.id);
  const [include, setInclude] = useState<Include>(initialInclude);
  const [busy, setBusy] = useState(false);
  const [writeFailures, setWriteFailures] = useState<WriteFailure[]>([]);
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
    void qc.invalidateQueries({ queryKey: qk.overview(book.id) });
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
  const readyIds = new Set(data?.ready_ids ?? []);
  const readyCount = readyIds.size;
  const count = include === "ready" ? readyCount : selectedCount;

  const shared = book.shared_at != null;
  const canShare = data != null && passed > 0;
  async function toggleShare(next: boolean) {
    setMsg(null);
    try {
      await share.mutateAsync(next);
    } catch (e) {
      setMsg({ tone: "error", text: errMsg(e, "無法更改分享設定，請稍後再試") });
    }
  }

  /** 重送失敗的寫入（修線 push 最新筆畫、位置重送）；還是失敗就留著提示。 */
  async function resend() {
    try {
      await retryFailedWrites(qc, book.id, writeFailures);
      setWriteFailures([]);
    } catch (e) {
      if (e instanceof BookWritesError) setWriteFailures(e.failures);
    }
  }

  async function download() {
    if (busy || count === 0) return;
    setBusy(true);
    setMsg(null);
    setWriteFailures([]);
    // 先讓伺服器等於畫面：排著的自動合成、位置與修線都寫完；有失敗就不匯出舊內容
    try {
      await beforeExport?.();
      await whenBookWritesIdle(qc, book.id);
    } catch (e) {
      if (e instanceof BookWritesError) setWriteFailures(e.failures);
      else setMsg({ tone: "error", text: `有修改尚未儲存（${errMsg(e, "儲存失敗")}），請稍後再試` });
      setBusy(false);
      return;
    }
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
      // 立刻 revoke 會讓 Safari／Firefox 取消下載
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setMsg({ tone: "ok", text: `已產生「${a.download}」` });
      // 匯出時後端可能重算並退回草稿
      void qc.invalidateQueries({ queryKey: qk.pages(book.id) });
      void qc.invalidateQueries({ queryKey: qk.exportSummary(book.id) });
      void qc.invalidateQueries({ queryKey: qk.overview(book.id) });
    } catch (e) {
      setMsg({
        tone: "error",
        text: e instanceof ApiError ? e.message : "匯出失敗，請稍後再試",
      });
    } finally {
      setBusy(false);
    }
  }

  // 這次不會印的頁（依目前的範圍）：ready 時還有「請看一下」但仍通過的頁
  const positions = new Map((pages.data ?? []).map((p) => [p.id, p.position]));
  const notPrinted: { page_id: number; position: number; label: string; warn: boolean }[] = [
    ...(include === "ready"
      ? (data?.exportable_ids ?? [])
          .filter((id) => !readyIds.has(id))
          .map((id) => ({ page_id: id, position: positions.get(id) ?? -1, label: "請看一下", warn: true }))
      : []),
    ...skipped
      .filter((s) => include !== "all" || s.reason === "no_selection")
      .map((s) => ({
        page_id: s.page_id,
        position: s.position,
        label: s.reason === "no_selection" ? "還沒有圖" : "還沒做完",
        warn: s.reason === "not_passed",
      })),
  ].sort((a, b) => a.position - b.position);

  let hint: string;
  if (summary.isLoading) hint = "讀取中…";
  else if (summary.isError) hint = "";
  else if (count === 0) hint = include !== "all" ? "還沒有可以印的頁面。" : "沒有已選圖的頁面可以匯出。";
  else hint = "約 10 秒";

  return (
    <>
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
              <span>可以印</span>
              <b data-testid="passed-count">{data ? readyCount : "–"}</b>
            </div>
            <div className={`${styles.stat} ${styles.warn}`}>
              <span>請看一下／還沒做完</span>
              <b data-testid="failed-count">{data ? data.total - readyCount : "–"}</b>
            </div>
          </div>
          <p className={styles.layout}>{sentence}</p>

          <fieldset className={styles.fieldset}>
            <legend className={styles.legend}>匯出範圍</legend>
            {(
              [
                ["ready", "可以印的頁面", `${readyCount} 頁，可直接送印`],
                ["all", "所有已經有圖的頁面", `${selectedCount} 頁，含請看一下和還沒做完的頁，試印用`],
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

          {notPrinted.length > 0 && (
            <div className={styles.skipped}>
              <b>不會印的頁面</b>
              <ul aria-label="不會印的頁面">
                {notPrinted.map((s) => (
                  <li key={s.page_id}>
                    <span className={styles.pageNo}>
                      {s.position >= 0 ? `第 ${s.position + 1} 頁` : "一頁"}
                    </span>
                    <span className={s.warn ? styles.warnText : undefined}>{s.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className={styles.share}>
        <label className={styles.shareRow}>
          <input
            type="checkbox"
            role="switch"
            checked={shared}
            disabled={share.isPending || (!shared && !canShare)}
            onChange={(e) => void toggleShare(e.target.checked)}
          />
          <b>分享給其他使用者</b>
        </label>
        <p className={styles.shareNote}>
          {!shared && data && passed === 0
            ? "至少完成一頁才能分享"
            : "其他使用者可以檢視並下載已完成的頁面，不能修改。"}
        </p>
      </div>

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
        {writeFailures.length > 0 ? (
          <div role="alert" className={styles.errorText}>
            {writeFailures.map((f) => {
              const pos = positions.get(f.pageId);
              return (
                <p key={`${f.kind}-${f.pageId}`}>
                  {pos != null ? `第 ${pos + 1} 頁` : "一頁"}的{f.kind === "edits" ? "修線" : "位置"}還沒存好{" "}
                  <Link href={fixHref(book.id, f.pageId)}>去處理</Link>
                </p>
              );
            })}
            <Button variant="secondary" size="md" onClick={() => void resend()}>
              重送
            </Button>
          </div>
        ) : msg ? (
          <span
            role={msg.tone === "error" ? "alert" : "status"}
            className={msg.tone === "error" ? styles.errorText : styles.msg}
          >
            {msg.text}
          </span>
        ) : (
          hint && <span className={styles.msg}>{hint}</span>
        )}
      </div>
    </>
  );
}
