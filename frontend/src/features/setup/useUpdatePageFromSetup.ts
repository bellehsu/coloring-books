import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Page } from "@/api/client";
import { qk } from "@/api/hooks";

export interface SetupPagePatch {
  scene_prompt?: string;
  action_prompt?: string;
  caption_zh?: string;
  caption_en?: string;
}

/**
 * 設定頁的「編輯」：from_setup 讓後端依改動標「要重畫」（場景→背景、動作→角色動作；文字不標）。
 * invalidate: pages, overview, producePlan, books
 */
export function useUpdatePageFromSetup(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, ...body }: SetupPagePatch & { pageId: number }) =>
      api.patch<Page>(`/pages/${pageId}?from_setup=true`, body),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
        qc.invalidateQueries({ queryKey: qk.overview(bookId) }),
        qc.invalidateQueries({ queryKey: qk.producePlan(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
      ]),
  });
}
