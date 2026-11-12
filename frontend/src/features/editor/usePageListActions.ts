import type { MutableRefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Page } from "@/api/client";
import { useCreatePage, useDeletePage, useReorderPages } from "@/api/hooks";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import { forgetDeletedPage, forgetPageId } from "@/features/editing/forgetPage";
import type { AutoCompose } from "./autoCompose";
import { DEFAULT_MODE, type EditorMode } from "./StageHeader";

export interface PageListActionsArgs {
  bookId: number;
  pages: Page[];
  current: Page | null;
  go: (page: number, mode: EditorMode) => void;
  selectPage: (id: number) => void;
  autoCompose: AutoCompose;
  /** 回覆這頁「清掉補過的線？」的提問（刪除時當作取消）。 */
  settleAsk: (pageId: number, ok: boolean) => void;
  /** 刪除請求已送出、還沒回來的頁（這期間回來的合成錯誤不提示）。 */
  deleting: MutableRefObject<Set<number>>;
}

/** 頁面清單（Filmstrip）的新增、調整順序與刪除。 */
export function usePageListActions({ bookId, pages, current, go, selectPage, autoCompose, settleAsk, deleting }: PageListActionsArgs) {
  const qc = useQueryClient();
  const toast = useToast();
  const createPage = useCreatePage(bookId);
  const deletePage = useDeletePage(bookId);
  const reorder = useReorderPages(bookId);

  const addPage = () =>
    createPage.mutate(
      { scene_prompt: "", action_prompt: "" },
      {
        onSuccess: (p) => {
          forgetPageId(qc, p.id); // 後端若沿用了已刪除頁的 id，不要讓舊頁的狀態串過來
          autoCompose.forget(p.id);
          go(p.id, DEFAULT_MODE);
        },
        onError: (e) => toast.show(errMsg(e, "無法新增頁面"), "error"),
      },
    );

  const movePage = (id: number, dir: -1 | 1) => {
    const ids = pages.map((p) => p.id);
    const i = ids.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder.mutate(ids, { onError: (e) => toast.show(errMsg(e, "無法調整順序"), "error") });
  };

  const removePage = (id: number) => {
    const i = pages.findIndex((p) => p.id === id);
    const neighbor = pages[i + 1] ?? pages[i - 1];
    autoCompose.forget(id); // 還在等的自動合成不送
    settleAsk(id, false);
    deleting.current.add(id);
    // 先切到相鄰頁（樂觀），避免刪除完成前後閃到別頁；失敗就留在相鄰頁並提示
    if (id === current?.id && neighbor) selectPage(neighbor.id);
    deletePage.mutate(id, {
      onSuccess: () => forgetDeletedPage(qc, id),
      onError: (e) => toast.show(errMsg(e, "無法刪除頁面"), "error"),
      onSettled: () => deleting.current.delete(id),
    });
  };

  return { addPage, movePage, removePage, adding: createPage.isPending };
}
