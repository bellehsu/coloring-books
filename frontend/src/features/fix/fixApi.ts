import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, type Generation, type Page, type Placement } from "@/api/client";
import { qk } from "@/api/hooks";
import type { components } from "@/api/schema";
import type { RestoreItem } from "./fixState";

type S = components["schemas"];

/**
 * C 處理一頁的請求：既有端點帶 `?oneclick=true`（後端先存復原快照、排頁面流程；「換一張／AI 再畫一張」的新圖
 * 好了一定換上去），以及 C 專用的 undo／redo／restore／accept。
 */
const C = "?oneclick=true";

/** C 的改動之後要重抓的東西：頁面、QA、筆畫、B 的總覽、生成紀錄與佇列、書與匯出統計。 */
export function refreshFix(qc: QueryClient, bookId: number, pageId: number) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
    qc.invalidateQueries({ queryKey: qk.qa(pageId) }),
    qc.invalidateQueries({ queryKey: qk.edits(pageId) }),
    qc.invalidateQueries({ queryKey: qk.overview(bookId) }),
    qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
    qc.invalidateQueries({ queryKey: qk.queue() }),
    qc.invalidateQueries({ queryKey: qk.book(bookId) }),
    qc.invalidateQueries({ queryKey: qk.books() }),
    qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) }),
  ]);
}

/** 回傳的 PageOut 先寫進 pages 快取（復原／重做按鈕立刻更新），再重抓。 */
function putPage(qc: QueryClient, bookId: number, page: Page) {
  qc.setQueryData<Page[]>(qk.pages(bookId), (old) => old?.map((p) => (p.id === page.id ? page : p)));
}

export const FIX_POLL_MS = 2000;

/**
 * 這本書的頁面（C 用）：這頁等著重新合成、或 busy（B 的總覽說這頁製作中、這頁有生成在跑）時每 2 秒重抓，
 * 預覽跟著更新。忙完的那一刻再重抓一次頁面、QA 與總覽。
 */
export function useFixPages(bookId: number, pageId: number, busy: boolean) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: qk.pages(bookId),
    queryFn: () => api.get<Page[]>(`/books/${bookId}/pages`),
    refetchInterval: (query) =>
      busy || query.state.data?.find((p) => p.id === pageId)?.recompose_pending ? FIX_POLL_MS : false,
  });
  const pending = !!q.data?.find((p) => p.id === pageId)?.recompose_pending;
  const working = busy || pending;
  const was = useRef(working);
  useEffect(() => {
    if (was.current && !working) void refreshFix(qc, bookId, pageId);
    was.current = working;
  }, [working, qc, bookId, pageId]);
  return q;
}

function usePageMutation<V, R extends Page = Page>(bookId: number, pageId: number, fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (page) => {
      putPage(qc, bookId, page);
      return refreshFix(qc, bookId, pageId);
    },
  });
}

export const useUndoPage = (bookId: number, pageId: number) =>
  usePageMutation<void>(bookId, pageId, () => api.post<Page>(`/pages/${pageId}/undo`));

export const useRedoPage = (bookId: number, pageId: number) =>
  usePageMutation<void>(bookId, pageId, () => api.post<Page>(`/pages/${pageId}/redo`));

export const useRestorePage = (bookId: number, pageId: number) =>
  usePageMutation<RestoreItem>(bookId, pageId, (item) =>
    api.post<Page>(`/pages/${pageId}/restore`, { item } satisfies S["RestoreIn"]),
  );

/** 「這頁可以印了 ✓」：清掉請看一下、標成可以印。 */
export const useAcceptPage = (bookId: number, pageId: number) =>
  usePageMutation<void>(bookId, pageId, () => api.post<Page>(`/pages/${pageId}/accept`));

/** 「再試一次」：只處理這一頁（重畫失敗的那張圖，或重跑這頁的流程）；回傳頁面＋做了什麼。 */
export const useRetryPage = (bookId: number, pageId: number) =>
  usePageMutation<void, S["RetryOut"]>(bookId, pageId, () => api.post<S["RetryOut"]>(`/pages/${pageId}/retry`));

/** 拖完角色（或翻轉、重設）：位置變了就存快照、重新合成。 */
export const useSavePlacementC = (bookId: number, pageId: number) =>
  usePageMutation<Placement>(bookId, pageId, (p) => api.put<Page>(`/pages/${pageId}/placement${C}`, p));

/** 改動作文字、改字（PATCH ?oneclick=true：內容變了先存快照）。 */
export const usePatchPageC = (bookId: number, pageId: number) =>
  usePageMutation<Pick<S["PageUpdate"], "action_prompt" | "caption_zh" | "caption_en">>(bookId, pageId, (body) =>
    api.patch<Page>(`/pages/${pageId}${C}`, body),
  );

/** 上傳圖片當背景（等於換背景：存快照、重新合成）。 */
export const useUploadBackgroundC = (bookId: number, pageId: number) =>
  usePageMutation<File>(bookId, pageId, (file) => api.upload<Page>(`/pages/${pageId}/backgrounds/upload${C}`, file));

/**
 * 「換一張」（角色動作，用目前的動作文字）／「AI 再畫一張」（背景）：排一張新的；帶 oneclick，
 * 後端在 request 記下 take，好了就換上去（並存復原快照）。
 */
export function useRegenerate(bookId: number, pageId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { kind: "background" } | { kind: "pose"; action: string }) =>
      v.kind === "background"
        ? api.post<Generation>(`/pages/${pageId}/backgrounds${C}`, {} satisfies S["BackgroundCreate"])
        : api.post<Generation>(`/pages/${pageId}/poses${C}`, { action: v.action } satisfies S["PoseCreate"]),
    onSuccess: () => refreshFix(qc, bookId, pageId),
  });
}
