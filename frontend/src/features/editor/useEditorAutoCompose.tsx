import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { qk, useCompose } from "@/api/hooks";
import type { Edits, Generation, Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { peekEditsQueue, pruneEditsQueues } from "@/features/editing/editsQueue";
import { errMsg } from "@/features/editing/errMsg";
import { forgetPageId } from "@/features/editing/forgetPage";
import { placementSaverFor } from "@/features/editing/placementSaver";
import { createAutoCompose } from "./autoCompose";
import styles from "./EditorPage.module.css";

export const hasBoth = (p: Page) => p.background_generation_id != null && p.pose_generation_id != null;

interface AutoComposeDeps {
  compose: (pageId: number) => Promise<void>;
  lineEdits: (pageId: number) => string | null;
  /** 要問這頁「清掉補過的線？」，回傳使用者的選擇（等到那頁在畫面上才問）。 */
  ask: (pageId: number) => Promise<boolean>;
}

/** 編輯器的 autoCompose：只建立一次（計時跨 render 與換頁），回呼由 effect 換成最新 render 的版本。 */
function createEditorAutoCompose() {
  let deps: AutoComposeDeps = { compose: async () => {}, lineEdits: () => "?", ask: async () => false };
  const ac = createAutoCompose({
    compose: (pageId) => deps.compose(pageId),
    lineEdits: (pageId) => deps.lineEdits(pageId),
    confirm: (pageId) => deps.ask(pageId),
  });
  return { ac, setDeps: (d: AutoComposeDeps) => void (deps = d) };
}

export interface EditorAutoComposeArgs {
  bookId: number;
  /** 這本書的頁面（pages 查詢的資料；還沒載入為 undefined）。 */
  pages: Page[] | undefined;
  current: Page | null;
  /** 目前頁的生成紀錄（含捨棄的）。 */
  currentGens: Generation[] | undefined;
  /** 目前頁補過的線（構圖時載入；載入後要再判斷一次要不要自動合成）。 */
  currentEdits: Edits | undefined;
  /** 合成前先讓那頁選定圖的修線寫完。 */
  flushSelectedEdits: (page: Page) => Promise<void>;
}

/**
 * 自動合成（§4.5）：位置或素材變了 → debounce → 等修線與位置寫完 → POST compose。
 * 這頁有補過的線時先問「清掉補過的線？」（lossDialog，只在那頁是目前頁時打開）。
 * 背景與角色動作都選好、但選定圖不是它們的合成圖時，開著這頁就自動合成一次。
 */
export function useEditorAutoCompose({
  bookId,
  pages,
  current,
  currentGens,
  currentEdits,
  flushSelectedEdits,
}: EditorAutoComposeArgs) {
  const qc = useQueryClient();
  const toast = useToast();
  const compose = useCompose(bookId);
  const placementSaver = placementSaverFor(qc);
  /** 刪除請求已送出、還沒回來的頁：這期間回來的合成錯誤（404）不提示。 */
  const deleting = useRef(new Set<number>());

  const pageOf = (pageId: number) => qc.getQueryData<Page[]>(qk.pages(bookId))?.find((p) => p.id === pageId);
  const composeNow = async (pageId: number) => {
    if (placementSaver.isGone(pageId)) return; // 頁面已刪除
    const page = pageOf(pageId);
    if (page) await flushSelectedEdits(page);
    try {
      await compose.mutateAsync({ pageId });
    } catch (e) {
      // 位置寫入失敗有自己的持續提示（位置尚未儲存＋重試），不再多跳 toast；
      // 頁面已刪除或正在刪除：合成回來的 404 是預期的，不提示
      const gone = placementSaver.isGone(pageId) || deleting.current.has(pageId);
      if (!placementSaver.failed(pageId) && !gone) toast.show(errMsg(e, "無法合成"), "error");
      throw e;
    }
  };
  /**
   * 這頁目前選定圖上補過的線的記號（「選圖 id:筆數」）；沒有補線回 null。
   * 還不知道（修線圖層還沒載入）就回「選圖 id:?」，當作有，寧可多問一次。
   * 自動合成的同意只對問的當時那份補線有效：合成失敗後又補了線，筆數變了就再問。
   */
  const lineEditsOf = (pageId: number): string | null => {
    const sel = pageOf(pageId)?.selected_generation_id;
    if (sel == null) return null;
    const local = peekEditsQueue(qc, pageId, sel)?.latest;
    const data = qc.getQueryData<Edits>(qk.edits(pageId));
    const count = local ? local.length : data?.generation_id === sel ? data.strokes.length : null;
    if (count === 0) return null;
    return `${sel}:${count ?? "?"}`;
  };
  /**
   * 等著問「清掉補過的線？」的頁 → 回覆給 autoCompose 的函式。每頁同時最多一個（每頁一次只跑一個合成）。
   * 只在那頁是目前頁時開對話框：拖完馬上換頁，要等使用者回到那頁才問（那頁的合成也等著）。
   */
  const [lossAsks, setLossAsks] = useState<ReadonlyMap<number, (ok: boolean) => void>>(() => new Map());
  const [auto] = useState(createEditorAutoCompose);
  const autoCompose = auto.ac;
  // 這頁等著重新合成（C 改過）：不合成，也就不用問補線（位置照常存，流程合成時用最新的位置）。
  // 自己合成會清掉 C 的等待、補線分頁打開，之後流程換圖時補的線就不見了
  const recomposing = (pageId: number) => !!pageOf(pageId)?.recompose_pending;
  useEffect(() => {
    auto.setDeps({
      compose: (pageId) => (recomposing(pageId) ? Promise.resolve() : composeNow(pageId)),
      lineEdits: (pageId) => (recomposing(pageId) ? null : lineEditsOf(pageId)),
      ask: (pageId) => new Promise<boolean>((resolve) => setLossAsks((m) => new Map(m).set(pageId, resolve))),
    });
  });
  useEffect(() => autoCompose.connect(), [autoCompose]);

  // 換選圖後，舊選圖閒置的修線佇列就不需要了；已刪除的 id 又出現在清單（被新頁沿用）→ 當成全新的頁
  useEffect(() => {
    for (const p of pages ?? []) {
      if (placementSaver.isGone(p.id)) {
        forgetPageId(qc, p.id);
        autoCompose.forget(p.id);
      }
      pruneEditsQueues(qc, p.id, p.selected_generation_id);
    }
  }, [qc, placementSaver, autoCompose, pages]);
  const composeStatus = useSyncExternalStore(
    autoCompose.subscribe,
    () => (current ? autoCompose.status(current.id) : "idle"),
    () => "idle" as const,
  );
  /** 回覆這頁的提問（使用者按了，或頁面刪除時當作取消）。 */
  const settleAsk = (pageId: number, ok: boolean) => {
    const resolve = lossAsks.get(pageId);
    if (!resolve) return;
    resolve(ok);
    setLossAsks((m) => {
      const next = new Map(m);
      next.delete(pageId);
      return next;
    });
  };
  const askingCurrent = current != null && lossAsks.has(current.id);

  // 背景與角色動作都選好、但選定圖不是它們的合成圖（「產生全部素材」自動選用、合成前離開等）：
  // 開著這頁時自動合成一次。舊流程圖片不自動換掉；有補過的線不自動問（等使用者動了才問）。
  const curGens = currentGens;
  const genOf = (id: number | null | undefined) => (id == null ? undefined : curGens?.find((g) => g.id === id));
  const curSel = genOf(current?.selected_generation_id);
  const autoKey =
    current &&
    hasBoth(current) &&
    current.compose_pending &&
    !current.recompose_pending &&
    genOf(current.background_generation_id)?.image_url &&
    genOf(current.pose_generation_id)?.cutout_url &&
    (current.selected_generation_id == null || curSel?.kind === "composite")
      ? `${current.id}:${current.background_generation_id}:${current.pose_generation_id}:${current.selected_generation_id}`
      : null;
  const autoDone = useRef(new Set<string>());
  useEffect(() => {
    if (!autoKey || !current || autoDone.current.has(autoKey)) return;
    if (autoCompose.status(current.id) !== "idle" || lineEditsOf(current.id) != null) return;
    autoDone.current.add(autoKey);
    autoCompose.schedule(current.id);
    // lineEditsOf 讀快取：修線圖層載入後要再判斷一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoKey, autoCompose, currentEdits]);

  /** 這頁還在等（或正在跑）的自動合成：做完的 promise；沒有在等就是 undefined（不用等）。 */
  const settle = (pageId: number): Promise<void> | undefined => {
    const st = autoCompose.status(pageId);
    return st === "pending" || st === "running" ? autoCompose.flush(pageId) : undefined;
  };
  /** 匯出前：所有頁還在等（或正在跑）的自動合成先做完，PDF 才是畫面上的內容。 */
  const settleAll = async () => {
    await Promise.all((pages ?? []).map((p) => settle(p.id)));
  };

  const lossDialog = (
    <Dialog open={askingCurrent} onClose={() => settleAsk(current!.id, false)} title="清掉補過的線？">
      <p className={styles.confirmText}>移動角色會清掉這頁補過的線，要繼續嗎？</p>
      <div className={styles.confirmActions}>
        <Button onClick={() => settleAsk(current!.id, false)}>取消</Button>
        <Button variant="primary" onClick={() => settleAsk(current!.id, true)}>
          繼續
        </Button>
      </div>
    </Dialog>
  );

  return { autoCompose, composeStatus, settleAsk, settle, settleAll, deleting, lossDialog };
}
