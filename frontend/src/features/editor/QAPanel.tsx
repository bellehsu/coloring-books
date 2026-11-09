"use client";

import { useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, type Book, type Generation, type Page, type QA } from "@/api/client";
import { qk, useAutofix, useCharacterCheck, usePass, useUnpass } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { peekEditsQueue } from "./editsQueue";
import { errMsg } from "./errMsg";
import styles from "./QA.module.css";

export const MAX_NOTE = 500;
export const MAX_REASON = 500;

export interface QAPanelProps {
  book: Book;
  page: Page;
  qa: QA | undefined;
  generation: Generation | undefined;
}

interface PassFailure {
  message: string;
  errors: string[];
}

/** pass 的 409 detail：字串，或 {message, errors:[{code, message}]}。 */
function passFailure(e: unknown): PassFailure {
  if (e instanceof ApiError && e.detail && typeof e.detail === "object" && !Array.isArray(e.detail)) {
    const d = e.detail as { message?: unknown; errors?: unknown };
    const errors = Array.isArray(d.errors)
      ? d.errors
          .map((x) => (x && typeof x === "object" ? (x as { message?: unknown }).message : null))
          .filter((m): m is string => typeof m === "string")
      : [];
    return { message: typeof d.message === "string" ? d.message : e.message, errors };
  }
  return { message: errMsg(e, "操作失敗，請再試一次"), errors: [] };
}

/** QA 模式的檢查面板：自動檢查、角色外型確認、通過／退回、下載本頁 PNG。 */
export function QAPanel(props: QAPanelProps) {
  // key：角色檢查是「每張圖」的；換頁或換選圖時，尚未儲存的註記草稿與強制通過原因直接捨棄。
  return <QAPanelInner key={`${props.page.id}:${props.page.selected_generation_id}`} {...props} />;
}

function QAPanelInner({ book, page, qa }: QAPanelProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const noteId = useId();
  const reasonId = useId();
  const autofix = useAutofix(book.id);
  const check = useCharacterCheck(book.id);
  const pass = usePass(book.id);
  const unpass = useUnpass(book.id);

  const [noteDraft, setNoteDraft] = useState<string | null>(null);
  const note = noteDraft ?? qa?.qa_note ?? "";
  const [forceOpen, setForceOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonErr, setReasonErr] = useState(false);
  const [failure, setFailure] = useState<PassFailure | null>(null);

  const genId = page.selected_generation_id;
  const passed = page.status === "passed";
  const saved = page.character_check;
  const result = check.isPending ? check.variables.result : saved;
  const errors = qa?.errors ?? [];
  const warnings = qa?.warnings ?? [];
  const hasErrors = errors.length > 0;
  // 從點擊到 mutation 結束都算送出中（flush 等待期間 isPending 還是 false）；ref 擋同一個事件循環內的連點。
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  function begin(): boolean {
    if (submittingRef.current) return false;
    submittingRef.current = true;
    setSubmitting(true);
    return true;
  }
  function end() {
    submittingRef.current = false;
    setSubmitting(false);
  }
  // 只有「換結果」的儲存會擋住通過；失焦時只存註記（結果不變）不能吃掉下一次點擊。
  const savingResult = check.isPending && check.variables.result !== saved;
  const busy = submitting || savingResult || autofix.isPending || pass.isPending || unpass.isPending;

  /** 先讓修線寫完；寫入失敗（已有 toast）就停止。 */
  async function flushEdits(): Promise<boolean> {
    if (genId == null) return true;
    try {
      await peekEditsQueue(qc, page.id, genId)?.flush();
      return true;
    } catch {
      toast.show("修線尚未儲存，請稍後再試", "error");
      return false;
    }
  }

  function submitCheck(r: "consistent" | "inconsistent", text = note) {
    if (genId == null) return;
    setFailure(null);
    check.mutate(
      { pageId: page.id, generation_id: genId, result: r, note: text },
      {
        onError: (e) => {
          toast.show(errMsg(e, "儲存角色檢查失敗"), "error");
          if (e instanceof ApiError && e.status === 409) void qc.invalidateQueries({ queryKey: qk.pages(book.id) });
        },
      },
    );
  }

  async function doAutofix() {
    if (genId == null || !begin()) return;
    if (!(await flushEdits())) return end();
    autofix.mutate(
      { pageId: page.id, generationId: genId },
      {
        onSettled: end,
        onSuccess: (d) => toast.show(d.autofix_added > 0 ? `已補 ${d.autofix_added} 條線` : "沒有可補的線", "success"),
        onError: (e) => toast.show(errMsg(e, "自動補線失敗"), "error"),
      },
    );
  }

  async function doPass(forceReason?: string) {
    if (!begin()) return;
    if (!(await flushEdits())) return end();
    setFailure(null);
    pass.mutate(
      { pageId: page.id, forceReason },
      {
        onSettled: end,
        onSuccess: () => setForceOpen(false),
        onError: (e) => {
          setForceOpen(false);
          const f = passFailure(e);
          setFailure(f);
          if (f.errors.length === 0) toast.show(f.message, "error");
        },
      },
    );
  }

  function doUnpass() {
    setFailure(null);
    unpass.mutate(page.id, { onError: (e) => toast.show(errMsg(e, "退回草稿失敗"), "error") });
  }

  function submitForce() {
    const r = reason.trim();
    if (!r) {
      setReasonErr(true);
      return;
    }
    void doPass(r);
  }

  const consistent = result === "consistent";
  const ready = consistent && !hasErrors && genId != null && qa != null;
  const canForce = !passed && consistent && hasErrors && genId != null;
  const hint = passed
    ? "這頁會出現在匯出的 PDF 裡。"
    : result === "inconsistent"
      ? "角色不一致不能通過，請換一張候選圖。"
      : hasErrors
        ? "還有缺口：修好再通過，或填寫原因強制通過。"
        : !consistent
          ? "請先確認角色外型。"
          : "可以通過了。";

  return (
    <div data-testid="qa-panel" className={styles.panel}>
      <section aria-labelledby={`${noteId}-auto`} className={styles.sec}>
        <h2 id={`${noteId}-auto`} className={styles.h2}>
          自動檢查
        </h2>
        {!qa ? (
          <div className={styles.status}>檢查中…</div>
        ) : hasErrors ? (
          <div className={styles.status} data-tone="err">
            <b>錯誤</b>
            <ul className={styles.statusList}>
              {errors.map((m, i) => (
                <li key={`${m.code}-${i}`}>{m.message}</li>
              ))}
            </ul>
          </div>
        ) : (
          <div className={styles.status} data-tone="ok">
            <b>通過</b>
            <span>線條都有封閉</span>
          </div>
        )}
        {qa && qa.gap_count > 0 && (
          <Button variant="secondary" className={styles.fixBtn} disabled={busy} onClick={doAutofix}>
            自動補線
          </Button>
        )}
        {warnings.length > 0 && (
          <ul className={styles.warnList}>
            {warnings.map((m, i) => (
              <li key={`${m.code}-${i}`}>{m.message}</li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby={`${noteId}-char`} className={styles.sec}>
        <h2 id={`${noteId}-char`} className={styles.h2}>
          角色外型（人工確認）
        </h2>
        <p className={styles.body}>臉、耳朵、衣服、顏色和參考圖一致嗎？有沒有多出來的角色？角色在綠色安全區內嗎？</p>
        <div role="radiogroup" aria-label="角色外型" className={styles.radios}>
          <button
            type="button"
            role="radio"
            aria-checked={result === "consistent"}
            data-on={result === "consistent"}
            data-kind="ok"
            className={styles.radio}
            disabled={genId == null || submitting}
            onClick={() => submitCheck("consistent")}
          >
            一致
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={result === "inconsistent"}
            data-on={result === "inconsistent"}
            data-kind="bad"
            className={styles.radio}
            disabled={genId == null || submitting}
            onClick={() => submitCheck("inconsistent")}
          >
            不一致
          </button>
        </div>
        <label className={styles.field}>
          註記（選填）
          <input
            type="text"
            className={styles.input}
            maxLength={MAX_NOTE}
            value={note}
            onChange={(e) => setNoteDraft(e.target.value)}
            onBlur={() => {
              if (result && noteDraft != null && noteDraft !== (qa?.qa_note ?? "")) submitCheck(result, noteDraft);
            }}
          />
        </label>
        {!result && note !== "" && <span className={styles.hint}>選擇一致或不一致後才會儲存註記</span>}
      </section>

      <section className={styles.secTight}>
        <button
          type="button"
          className={styles.passBtn}
          data-passed={passed}
          disabled={passed ? busy : !ready || busy}
          onClick={passed ? doUnpass : () => doPass()}
        >
          {passed ? "已通過・點此退回草稿" : "標為通過"}
        </button>
        <span className={styles.hint}>{hint}</span>
        {failure && (
          <ul role="alert" className={styles.errBox}>
            <li>{failure.message}</li>
            {failure.errors.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        )}
        {canForce && (
          <button type="button" className={styles.link} onClick={() => setForceOpen(true)}>
            強制通過…
          </button>
        )}
        <a href={`/api/pages/${page.id}/print.png`} download className={styles.link}>
          <Icons.download size={16} />
          下載本頁 PNG
        </a>
      </section>

      <Dialog open={forceOpen} onClose={() => setForceOpen(false)} title="強制通過">
        <div className={styles.dialogBody}>
          <p className={styles.body}>線稿還有缺口。請寫下為什麼仍要通過，這段原因會記錄在本頁。</p>
          <label htmlFor={reasonId} className={styles.field}>
            原因（必填）
            <textarea
              id={reasonId}
              className={styles.textarea}
              maxLength={MAX_REASON}
              value={reason}
              aria-invalid={reasonErr}
              onChange={(e) => {
                setReason(e.target.value);
                setReasonErr(false);
              }}
            />
          </label>
          <span className={styles.counter}>
            {reason.length} / {MAX_REASON}
          </span>
          {reasonErr && (
            <span role="alert" className={styles.hint}>
              請填寫原因
            </span>
          )}
          <div className={styles.dialogActions}>
            <Button variant="ghost" onClick={() => setForceOpen(false)}>
              取消
            </Button>
            <Button variant="primary" disabled={busy} onClick={submitForce}>
              強制通過
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
