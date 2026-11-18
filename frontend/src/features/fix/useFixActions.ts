import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import type { OverviewPage, Page } from "@/api/client";
import { useToast } from "@/components/ui/Toast";
import { flushAllEditsFor } from "@/features/editing/editsQueue";
import { errMsg } from "@/features/editing/errMsg";
import { onBeforeUnload } from "@/features/editing/pendingWrites";
import { useSingleFlight } from "@/lib/useSingleFlight";
import { useAcceptPage, useRedoPage, useRestorePage, useRetryPage, useSelectPoseC, useUndoPage } from "./fixApi";
import { fixHref, type FixFilter } from "@/lib/fixHref";
import type { RestoreItem } from "./fixState";
import type { PlacementSaves } from "./usePlacementSaves";

export interface FixActionsArgs {
  bookId: number;
  page: Page;
  filter: FixFilter;
  /** 「這頁可以印了」之後去的頁（沒有就回整本書）。 */
  next: OverviewPage | null;
  placement: PlacementSaves;
  /** 復原／重做／還原前收起畫筆。 */
  closePen: () => void;
}

/**
 * C 頁首與側欄的動作：復原／重做／還原、換成畫過的另一張角色動作、這頁可以印了、再試一次（一次只做一件事；失敗 toast），
 * 以及文字欄位存檔的追蹤（trackSave）與關分頁前的確認。
 */
export function useFixActions({ bookId, page, filter, next, placement, closePen }: FixActionsArgs) {
  const pageId = page.id;
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();

  const undoM = useUndoPage(bookId, pageId);
  const redoM = useRedoPage(bookId, pageId);
  const restoreM = useRestorePage(bookId, pageId);
  const acceptM = useAcceptPage(bookId, pageId);
  const retryM = useRetryPage(bookId, pageId);
  const selectPoseM = useSelectPoseC(bookId, pageId);

  const flight = useSingleFlight();
  // 復原／重做／還原成功一次就加 1：文字欄位（動作、場景、圖下方的字）丟掉改過的值、改顯示伺服器的
  const [historyEpoch, setHistoryEpoch] = useState(0);
  // 文字欄位離開時的存檔與「中文／英文」開關的 PATCH（送出中）：復原／重做／還原、可以印、再試一次先等它們
  const pendingSaves = useRef(new Set<Promise<unknown>>());
  const [textSaving, setTextSaving] = useState(false);
  const trackSave = useCallback((p: Promise<unknown>): Promise<unknown> => {
    const set = pendingSaves.current;
    set.add(p);
    setTextSaving(true);
    const done = () => {
      set.delete(p);
      setTextSaving(set.size > 0);
    };
    p.then(done, done);
    return p;
  }, []);
  const textSaves = () => Promise.allSettled([...pendingSaves.current]);

  // 關分頁／重新整理：修線（送出中、失敗）、位置或文字還沒存好就請瀏覽器確認
  const placementPending = placement.hasPending;
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => onBeforeUnload(qc, e, placementPending() || pendingSaves.current.size > 0);
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [qc, placementPending]);

  /** 送出前先讓畫面上的筆畫寫完（C 與進階編輯的佇列都等；否則晚到的筆畫會蓋掉復原的結果）。 */
  async function flushEdits() {
    const gid = page.selected_generation_id;
    if (gid == null) return;
    await flushAllEditsFor(qc, pageId, gid).catch(() => {});
  }

  /** 一次只做一件事（復原、重做、還原、可以印、再試一次）；失敗 toast。 */
  const act = (fn: () => Promise<unknown>, fallback: string) =>
    flight.run(async () => {
      try {
        await fn();
      } catch (e) {
        toast.show(errMsg(e, fallback), "error");
      }
    });

  /** 復原／重做／還原：先等文字、拖拉的位置與筆畫寫完（晚到的寫入不會蓋掉復原），再收起畫筆
   * （畫布的筆畫在之後重新從伺服器載入）。 */
  const runHistory = (fn: () => Promise<unknown>, fallback: string) =>
    act(async () => {
      await textSaves();
      await placement.idle();
      await flushEdits();
      closePen();
      await fn(); // 回傳的頁面已寫進快取（putPage），欄位重設後顯示的就是復原後的文字
      setHistoryEpoch((e) => e + 1);
    }, fallback);

  const undo = () => runHistory(() => undoM.mutateAsync(), "沒辦法復原");
  const redo = () => runHistory(() => redoM.mutateAsync(), "沒辦法重做");
  const restore = (item: RestoreItem) => runHistory(() => restoreM.mutateAsync(item), "沒辦法還原");

  /** 點「畫過的角色動作」裡的一張：先等文字、位置與筆畫寫完，再換上去（後端先存復原快照）。 */
  const selectPose = (generationId: number) =>
    act(async () => {
      await textSaves();
      await placement.idle();
      await flushEdits();
      await selectPoseM.mutateAsync(generationId);
    }, "無法換成這張角色動作");

  const accept = () =>
    act(async () => {
      await textSaves();
      await placement.idle();
      await flushEdits();
      await acceptM.mutateAsync();
      router.push(next ? fixHref(bookId, next.id, filter) : `/books/${bookId}`);
    }, "沒辦法標成可以印");

  /** 「再試一次」：只處理這一頁（重畫失敗的那張，或重跑這頁的流程）。 */
  const retry = () =>
    act(async () => {
      await textSaves();
      const r = await retryM.mutateAsync();
      if (r.requeued.length > 0) {
        const what = r.requeued.map((k) => (k === "pose" ? "角色動作" : "背景")).join("和");
        toast.show(`重新畫這一頁的${what}了，好了會自動換上去`, "info");
      } else toast.show("重新做這一頁了，好了會自動更新", "info");
    }, "沒辦法再試一次");

  // 文字存檔中不停用按鈕（只標 aria-busy）：按下按鈕時欄位 blur 才送出存檔，停用會吃掉「改完直接按」的那一下；
  // 復原／重做／還原、可以印、再試一次自己會先等文字存好（textSaves）
  const busy = flight.busy || undoM.isPending || redoM.isPending || placement.saving;
  const acceptBlocked = !page.recompose_pending
    ? null
    : page.attention === "failed"
      ? "這一頁合成失敗，請先按「再試一次」再印"
      : "這一頁還在重新合成，等它做完再按";

  return {
    busy,
    textSaving,
    trackSave,
    historyEpoch,
    flushEdits,
    undo,
    redo,
    restore,
    selectPose,
    accept,
    retry,
    acceptBlocked,
  };
}
