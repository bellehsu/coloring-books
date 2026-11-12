"use client";

import { useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, type Book, type Page, type QA } from "@/api/client";
import { qk, useAutofix, useCharacterCheck, useUnpass } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icons } from "@/components/ui/Icons";
import { Segmented } from "@/components/ui/Segmented";
import { useToast } from "@/components/ui/Toast";
import { useAcceptPage } from "@/features/fix/fixApi";
import { flushAllEditsFor, peekEditsQueue } from "@/features/editing/editsQueue";
import { errMsg } from "@/features/editing/errMsg";
import styles from "./FinishPanel.module.css";

type Detail = Page["line_detail"];
type LinePatch = { line_detail?: Detail; line_width_px?: number };

export const WIDTH_MIN = 8;
export const WIDTH_MAX = 16;

/** 300 DPI 的像素換成 mm（1 位小數）。 */
export const pxToMm = (px: number) => ((px / 300) * 25.4).toFixed(1);

const DETAILS: { value: Detail; label: string }[] = [
  { value: "simple", label: "簡單" },
  { value: "standard", label: "標準" },
  { value: "detailed", label: "細緻" },
];

export interface FinishPanelProps {
  book: Book;
  page: Page;
  /** 本頁 QA；載入中為 undefined。 */
  qa: QA | undefined;
  /** PATCH 本頁的線條設定；失敗時呼叫端顯示 toast 並 reject。 */
  onPatch: (body: LinePatch) => Promise<unknown>;
  /** 角色「不太像，換一張」記錄後切到 ① 構圖。 */
  onGoCompose: () => void;
}

/**
 * 「② 補線」右欄（§4.5）：缺口檢查＋自動補線、線條多寡（進階：線寬）、角色看起來對嗎、完成這一頁。
 * 有缺口也能完成：先問一次。「完成這一頁」和 C 的「這頁可以印了 ✓」同一個端點（POST /accept：清掉請看一下、
 * 等著重新合成時拒絕）；通過但 C 說請看一下的頁不算完成。
 */
export function FinishPanel(props: FinishPanelProps) {
  // key：角色檢查是「每張圖」的；換頁或換選圖時，本機狀態（線條設定、確認框）重來
  return <FinishPanelInner key={`${props.page.id}:${props.page.selected_generation_id}`} {...props} />;
}

function FinishPanelInner({ book, page, qa, onPatch, onGoCompose }: FinishPanelProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const id = useId();
  const autofix = useAutofix(book.id);
  const check = useCharacterCheck(book.id);
  const accept = useAcceptPage(book.id, page.id);
  const unpass = useUnpass(book.id);

  const genId = page.selected_generation_id;
  const passed = page.status === "passed" && !page.attention;
  const saved = page.character_check;
  const result = check.isPending ? check.variables.result : saved;
  const consistent = result === "consistent";
  const gapCount = qa?.gap_count ?? 0;
  const warnings = qa?.warnings ?? [];

  /** 有缺口時「確定完成嗎？」框裡的缺口數；null＝框沒開。 */
  const [confirmGaps, setConfirmGaps] = useState<number | null>(null);

  // 從點擊到 mutation 結束都算送出中（等修線寫完期間 isPending 還是 false）；ref 擋同一個事件循環內的連點。
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
  // 只有「換答案」的儲存會擋住完成；同一個答案重送不擋
  const savingResult = check.isPending && check.variables.result !== saved;
  const busy = submitting || savingResult || accept.isPending || unpass.isPending;

  /** 先讓修線寫完（進階編輯與 C 的佇列都等）；寫入失敗（已有 toast）就停止。 */
  async function flushEdits(): Promise<boolean> {
    if (genId == null) return true;
    try {
      await flushAllEditsFor(qc, page.id, genId);
      return true;
    } catch {
      toast.show("修線尚未儲存，請稍後再試", "error");
      return false;
    }
  }

  async function doAutofix() {
    if (genId == null || !begin()) return;
    try {
      // 修線畫布掛著：用它的補線（先讓畫面上的筆畫寫完、補線期間延後寫入；補上的線存在 auto 圖層）
      const run = peekEditsQueue(qc, page.id, genId)?.autofix;
      let added: number | null = null;
      if (run) {
        added = await run();
      } else if (await flushEdits()) {
        try {
          added = (await autofix.mutateAsync({ pageId: page.id, generationId: genId })).autofix_added;
        } catch (e) {
          toast.show(errMsg(e, "自動補線失敗"), "error");
        }
      }
      if (added != null) toast.show(added > 0 ? `已補 ${added} 條線` : "沒有可自動補的缺口", "success");
    } finally {
      end();
    }
  }

  function answer(r: "consistent" | "inconsistent") {
    if (genId == null) return;
    check.mutate(
      { pageId: page.id, generation_id: genId, result: r, note: qa?.qa_note ?? "" },
      {
        onSuccess: () => {
          if (r === "inconsistent") onGoCompose();
        },
        onError: (e) => {
          toast.show(errMsg(e, "儲存角色檢查失敗"), "error");
          if (e instanceof ApiError && e.status === 409) void qc.invalidateQueries({ queryKey: qk.pages(book.id) });
        },
      },
    );
  }

  /** 「完成這一頁 ✓」：先讓修線寫完，用寫完後的缺口數決定要不要先問一次。 */
  async function clickFinish() {
    if (!begin()) return;
    if (!(await flushEdits())) return end();
    const gaps = (genId != null ? qc.getQueryData<QA>(qk.qa(page.id)) : undefined)?.gap_count ?? gapCount;
    if (gaps > 0) {
      setConfirmGaps(gaps);
      return end();
    }
    end();
    await doAccept();
  }

  async function doAccept() {
    if (!begin()) return;
    try {
      if (!(await flushEdits())) return;
      await accept.mutateAsync();
    } catch (e) {
      toast.show(errMsg(e, "無法完成這一頁"), "error");
    } finally {
      setConfirmGaps(null);
      end();
    }
  }

  function doUnpass() {
    unpass.mutate(page.id, { onError: (e) => toast.show(errMsg(e, "取消完成失敗"), "error") });
  }

  // ---------- 線條多寡（立即存）與線寬（放開時存）：線稿即時重算，不重新生成 ----------
  const [detail, setDetail] = useState<Detail>(page.line_detail);
  const [width, setWidth] = useState(page.line_width_px);
  const sentWidth = useRef<number>(page.line_width_px);

  function changeDetail(d: Detail) {
    const prev = detail;
    setDetail(d);
    onPatch({ line_detail: d }).catch(() => setDetail(prev));
  }

  function commitWidth() {
    if (width === sentWidth.current) return;
    const prev = sentWidth.current;
    sentWidth.current = width;
    onPatch({ line_width_px: width }).catch(() => {
      sentWidth.current = prev;
      setWidth(prev);
    });
  }

  const finishNoteId = `${id}-finish-note`;

  return (
    <div data-testid="finish-panel" className={styles.panel}>
      <section aria-label="線條檢查" className={styles.card}>
        {!qa ? (
          <p role="status" className={styles.headline}>
            檢查中…
          </p>
        ) : gapCount > 0 ? (
          <>
            <p role="status" className={styles.headline} data-tone="gap">
              還有 {gapCount} 個缺口
            </p>
            <p className={styles.hint}>橘色圈起來的地方線條沒有接起來，小朋友塗色時顏色會漏出去。</p>
            <button
              type="button"
              className={styles.autofix}
              disabled={busy || genId == null}
              aria-busy={submitting}
              onClick={() => void doAutofix()}
            >
              <Icons.wand size={18} />
              自動補線
            </button>
            <p className={styles.hint}>自動補不好的，用「補線」筆在畫面上畫一筆接起來。</p>
          </>
        ) : (
          <>
            <p role="status" className={styles.headline} data-tone="ok">
              ✓ 線條都接好了
            </p>
            <p className={styles.hint}>每個區塊都是封閉的，可以放心塗色。</p>
          </>
        )}
        {warnings.length > 0 && (
          <ul aria-label="其他提醒" className={styles.warnList}>
            {warnings.map((m, i) => (
              <li key={`${m.code}-${i}`}>{m.message}</li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby={`${id}-line`} className={styles.card}>
        <h2 id={`${id}-line`} className={styles.title}>
          線條多寡
        </h2>
        <Segmented kind="radio" label="線條多寡" value={detail} onChange={changeDetail} options={DETAILS} />
        <details className={styles.advanced}>
          <summary className={styles.summary}>進階：線寬</summary>
          <div className={styles.field}>
            <label htmlFor={`${id}-w`} className={styles.rangeLabel}>
              線寬{" "}
              <span className={styles.value}>
                {width} px・約 {pxToMm(width)} mm
              </span>
            </label>
            <input
              id={`${id}-w`}
              type="range"
              min={WIDTH_MIN}
              max={WIDTH_MAX}
              step={1}
              value={width}
              className={styles.range}
              onChange={(e) => setWidth(Number(e.target.value))}
              onPointerUp={commitWidth}
              onKeyUp={commitWidth}
              onBlur={commitWidth}
            />
            <p className={styles.hint}>補過的線存成獨立圖層，調整線條多寡或線寬不會讓它們消失。</p>
          </div>
        </details>
      </section>

      <section aria-labelledby={`${id}-char`} className={styles.card}>
        <h2 id={`${id}-char`} className={styles.title}>
          角色看起來對嗎？
        </h2>
        <p className={styles.hint}>臉、耳朵、衣服跟角色一樣，而且沒有多出別的角色。</p>
        <div className={styles.answers}>
          <button
            type="button"
            aria-pressed={result === "consistent"}
            data-kind="ok"
            className={styles.answer}
            disabled={genId == null || submitting}
            onClick={() => answer("consistent")}
          >
            對
          </button>
          <button
            type="button"
            aria-pressed={result === "inconsistent"}
            data-kind="bad"
            className={styles.answer}
            disabled={genId == null || submitting}
            onClick={() => answer("inconsistent")}
          >
            不太像，換一張
          </button>
        </div>
      </section>

      {passed ? (
        <div className={styles.finish}>
          <p className={styles.done}>
            <Icons.check size={18} />
            已完成
          </p>
          <Button disabled={busy} onClick={doUnpass}>
            取消完成
          </Button>
        </div>
      ) : (
        <div className={styles.finish}>
          <Button
            variant="primary"
            size="lg"
            disabled={!consistent || genId == null || qa == null || busy}
            aria-describedby={!consistent ? finishNoteId : undefined}
            onClick={() => void clickFinish()}
          >
            完成這一頁 ✓
          </Button>
          {!consistent && (
            <span id={finishNoteId} className={styles.note}>
              請先回答角色對不對
            </span>
          )}
        </div>
      )}
      <a href={`/api/pages/${page.id}/print.png`} download className={styles.link}>
        <Icons.download size={16} />
        下載本頁 PNG
      </a>

      <Dialog open={confirmGaps != null} onClose={() => setConfirmGaps(null)} title="完成這一頁？">
        <div className={styles.dialogBody}>
          <p className={styles.body}>還有 {confirmGaps} 個缺口，小朋友塗色時顏色可能漏出去。確定完成嗎？</p>
          <div className={styles.dialogActions}>
            <Button onClick={() => setConfirmGaps(null)}>再補補看</Button>
            <Button variant="primary" disabled={busy} onClick={() => void doAccept()}>
              完成
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
