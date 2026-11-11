"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { QA } from "@/api/client";
import { qk, useAutofix, useEdits } from "@/api/hooks";
import { useToast } from "@/components/ui/Toast";
import type { Stroke, StrokeOp } from "@/lib/strokes";
import { editsQueueFor, type EditsEntry } from "./editsQueue";
import { errMsg } from "./errMsg";
import { LineFixCanvas } from "./LineFixCanvas";
import styles from "./EditorPage.module.css";

export interface LineFixStageProps {
  bookId: number;
  pageId: number;
  /** 目前 selected_generation_id。 */
  genId: number;
  /** 本頁 QA（含 lineart_url 與 gaps）。 */
  qa: QA;
  contentPx: readonly [number, number];
  lineWidthPx: number;
  /** 不給：畫布自己管縮放（−／＋ 在畫布工具列）。 */
  zoom?: number;
  onZoomChange?: (z: number) => void;
  /** C 處理一頁：筆畫與自動補線帶 ?oneclick=true（另一個寫入佇列）。 */
  oneclick?: boolean;
  /** 畫布一開始用的工具（C 的「畫筆」「擦除」）。 */
  initialTool?: StrokeOp;
  /** 不顯示畫布工具列的「復原」（C 的頁首已有整頁的「↶ 復原」）。 */
  hideUndo?: boolean;
}

/**
 * 修線舞台：載入修改圖層後掛上 LineFixCanvas（QA 找到的缺口畫成橘色圈，位置用 qa.gaps）。
 * 自動補線的按鈕在右欄（FinishPanel），經 entry.autofix 呼叫這裡的補線。補上的線存在伺服器的 auto 圖層，
 * 只出現在重新渲染的線稿圖上，不併進畫面上（可撤銷、會被 PUT 整份覆蓋）的使用者筆畫。
 * 寫入走 editsQueueFor（每頁＋選圖一個、跨重新掛載共用）；佇列忙碌或上次寫入失敗時用本機最新筆畫，
 * 不從伺服器（可能還沒追上的）edits 重設。
 */
export function LineFixStage(props: LineFixStageProps) {
  const { bookId, pageId, genId } = props;
  const qc = useQueryClient();
  const edits = useEdits(pageId);
  const entry = editsQueueFor(qc, bookId, pageId, genId, { oneclick: props.oneclick });

  const key = `${pageId}-${genId}`;
  const local = entry.shouldSeedFromLocal() ? entry.latest : null;
  // 第一次 seed 時，重抓中的快取可能是舊的（例如畫布不在時 QA 面板自動補線），要等重抓完成。
  // 一旦這個（頁, 選圖）的編輯器掛上，就不再因背景重抓（自動補線、改線寬）卸載它。
  const server =
    !edits.isFetching && edits.data && edits.data.generation_id === genId ? (edits.data.strokes as Stroke[]) : null;
  // 只存「目前這個 key」的 seed：key 一變（換頁或換選圖）就換掉或清掉，回來時重新走第一次 seed 的規則，
  // 不會拿到離開前的舊 seed（期間畫的筆畫會不見，下一筆還會把它們蓋掉）。
  // （render 中依條件 setState 是 React 允許的「依 props 調整 state」寫法；ref 在 render 中讀寫會被 lint 擋。）
  const [seeded, setSeeded] = useState<{ key: string; strokes: Stroke[] } | null>(null);
  const fresh = local ?? server;
  if (seeded?.key !== key) {
    if (fresh) setSeeded({ key, strokes: fresh });
    else if (seeded) setSeeded(null);
  }
  const stored = seeded?.key === key ? seeded.strokes : null;
  // 佇列裡的最新筆畫（還在送或送失敗）永遠優先於存下的 seed
  const seed = local ?? stored ?? server;

  if (!seed) {
    return edits.isError ? (
      <p role="alert" className={styles.stageMsg}>
        {errMsg(edits.error, "無法載入修線圖層")}
      </p>
    ) : (
      <p className={styles.stageMsg} aria-busy="true">
        載入修線圖層中…
      </p>
    );
  }
  return <LineFixEditor key={key} {...props} entry={entry} initial={seed} />;
}

function LineFixEditor({
  bookId,
  pageId,
  genId,
  qa,
  contentPx,
  lineWidthPx,
  zoom,
  onZoomChange,
  oneclick,
  initialTool,
  hideUndo,
  entry,
  initial,
}: LineFixStageProps & { entry: EditsEntry; initial: Stroke[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const autofix = useAutofix(bookId);
  const [strokes, setStrokes] = useState<Stroke[]>(initial);
  const latest = useRef<Stroke[]>(initial);
  const fixing = useRef(false);
  /** 自動補線期間有修改（延後到結束才送出）。 */
  const changedWhileFixing = useRef(false);
  const [fixBusy, setFixBusy] = useState(false);
  const busy = useSyncExternalStore(entry.subscribe, entry.busy, entry.busy);
  const failed = useSyncExternalStore(
    entry.subscribe,
    () => entry.failed,
    () => entry.failed,
  );

  useEffect(() => {
    entry.setErrorHandler((e) => toast.show(`${errMsg(e, "儲存失敗")}，修線尚未儲存`, "error"));
  }, [entry, toast]);

  function onChange(next: Stroke[]) {
    latest.current = next;
    setStrokes(next);
    // 自動補線進行中先不寫入（補線依送出當下的線稿找缺口），結束後再送。
    if (fixing.current) changedWhileFixing.current = true;
    else entry.push(next);
  }

  /** 重送畫面上的完整筆畫（＝佇列最新的筆畫）。補線中就等補線結束再一起送。 */
  function retrySave() {
    if (fixing.current) changedWhileFixing.current = true;
    else entry.push(latest.current);
  }

  /** 自動補線（由右欄的「自動補線」經 entry.autofix 呼叫）：回傳補了幾條；失敗提示後回傳 null。
   * 補上的線在伺服器的 auto 圖層（新的 qa.lineart_url 已含），使用者的筆畫不變。 */
  async function runAutofix(): Promise<number | null> {
    if (fixing.current) return null;
    fixing.current = true;
    setFixBusy(true);
    changedWhileFixing.current = false;
    try {
      try {
        await entry.flush(); // 先讓伺服器等於畫面，補線才會依使用者目前的修線找缺口
      } catch (e) {
        toast.show(`${errMsg(e, "修線尚未儲存")}，請再畫一筆後再試`, "error");
        return null;
      }
      const res = await autofix.mutateAsync({ pageId, generationId: genId, oneclick });
      const { autofix_added: added, ...rest } = res;
      qc.setQueryData<QA>(qk.qa(pageId), rest as QA);
      return added;
    } catch (e) {
      toast.show(errMsg(e, "自動補線失敗"), "error");
      return null;
    } finally {
      fixing.current = false;
      setFixBusy(false);
      // 補線期間有修改 → 送出畫面上的完整筆畫；沒有的話伺服器已經等於畫面
      if (changedWhileFixing.current) entry.push(latest.current);
      changedWhileFixing.current = false;
    }
  }

  // 登記給右欄的「自動補線」：畫布掛著時一定要經過這裡，補上的線才會併進畫面上的筆畫
  const runRef = useRef(runAutofix);
  useEffect(() => {
    runRef.current = runAutofix;
  });
  useEffect(() => {
    const run = () => runRef.current();
    entry.setAutofix(run);
    return () => {
      if (entry.autofix === run) entry.setAutofix(null);
    };
  }, [entry]);

  return (
    <div className={styles.lineStage}>
      <LineFixCanvas
        lineartUrl={qa.lineart_url}
        contentPx={contentPx}
        gaps={qa.gaps}
        strokes={strokes}
        onChange={onChange}
        lineWidthPx={lineWidthPx}
        zoom={zoom}
        onZoomChange={onZoomChange}
        initialTool={initialTool}
        hideUndo={hideUndo}
        pending={busy || fixBusy}
        statusOverride={
          failed ? (
            <span data-testid="edits-save-failed" className={styles.saveFailed}>
              修線尚未儲存
              <button type="button" className={styles.retry} onClick={retrySave}>
                重試
              </button>
            </span>
          ) : undefined
        }
      />
    </div>
  );
}
