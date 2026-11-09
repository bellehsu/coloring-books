import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import type { components } from "./schema";
import type { Book, Edits, ExportSummary, Generation, Page, QA, QueueStatus } from "./client";

type S = components["schemas"];

/** 所有 query key 集中在這裡；其他程式碼要 invalidate 一律用 qk。 */
export const qk = {
  books: () => ["books"] as const,
  book: (id: number) => ["book", id] as const,
  pages: (bookId: number) => ["pages", bookId] as const,
  /** 前綴 key：涵蓋該書所有 useGenerations 篩選組合。 */
  generationsOf: (bookId: number) => ["generations", bookId] as const,
  generations: (bookId: number, f: GenFilter = {}) =>
    ["generations", bookId, f.kind ?? null, f.pageId ?? null, !!f.includeDiscarded] as const,
  queue: () => ["queue"] as const,
  qa: (pageId: number) => ["qa", pageId] as const,
  edits: (pageId: number) => ["edits", pageId] as const,
  exportSummary: (bookId: number) => ["exportSummary", bookId] as const,
};

export interface GenFilter {
  kind?: "character" | "page";
  pageId?: number;
  includeDiscarded?: boolean;
}

const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

// ---------- queries ----------

export const useBooks = () => useQuery({ queryKey: qk.books(), queryFn: () => api.get<Book[]>("/books") });

export const useBook = (id: number) =>
  useQuery({ queryKey: qk.book(id), queryFn: () => api.get<Book>(`/books/${id}`) });

export const usePages = (bookId: number) =>
  useQuery({ queryKey: qk.pages(bookId), queryFn: () => api.get<Page[]>(`/books/${bookId}/pages`) });

/** 有 queued/running 的 generation 時每 2 秒輪詢，否則停止。 */
export function useGenerations(bookId: number, f: GenFilter = {}) {
  return useQuery({
    queryKey: qk.generations(bookId, f),
    queryFn: () => {
      const q = new URLSearchParams();
      if (f.kind) q.set("kind", f.kind);
      if (f.pageId !== undefined) q.set("page_id", String(f.pageId));
      if (f.includeDiscarded) q.set("include_discarded", "true");
      const qs = q.toString();
      return api.get<Generation[]>(`/books/${bookId}/generations${qs ? `?${qs}` : ""}`);
    },
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 2000 : false),
  });
}

/** 佇列狀態每 5 秒輪詢。 */
export const useQueue = () =>
  useQuery({
    queryKey: qk.queue(),
    queryFn: () => api.get<QueueStatus>("/system/queue"),
    refetchInterval: 5000,
  });

export const useQA = (pageId: number, enabled = true) =>
  useQuery({ queryKey: qk.qa(pageId), queryFn: () => api.get<QA>(`/pages/${pageId}/qa`), enabled });

export const useEdits = (pageId: number, enabled = true) =>
  useQuery({
    queryKey: qk.edits(pageId),
    queryFn: () => api.get<Edits>(`/pages/${pageId}/lineart/edits`),
    enabled,
  });

export const useExportSummary = (bookId: number, enabled = true) =>
  useQuery({
    queryKey: qk.exportSummary(bookId),
    queryFn: () => api.get<ExportSummary>(`/books/${bookId}/export/summary`),
    enabled,
  });

// ---------- mutations ----------

/** 頁面內容（線稿、QA、通過狀態）改變後要重抓的東西。 */
function invalidatePage(qc: QueryClient, bookId: number, pageId: number) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
    qc.invalidateQueries({ queryKey: qk.qa(pageId) }),
    qc.invalidateQueries({ queryKey: qk.edits(pageId) }),
    qc.invalidateQueries({ queryKey: qk.book(bookId) }),
    qc.invalidateQueries({ queryKey: qk.books() }),
    qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) }),
  ]);
}

/** 新增任務後：generations（開始輪詢）與 queue。 */
function invalidateJobs(qc: QueryClient, bookId: number) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
    qc.invalidateQueries({ queryKey: qk.queue() }),
  ]);
}

/** invalidate: books */
export function useCreateBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: S["BookCreate"]) => api.post<Book>("/books", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.books() }),
  });
}

/** invalidate: book, books（紙張或模式改變也影響頁面版面與 QA） */
export function useUpdateBook(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: S["BookUpdate"]) => api.patch<Book>(`/books/${bookId}`, b),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
        qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
      ]),
  });
}

/** invalidate: generations, queue */
export function useCharacterCandidates(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: S["CharacterCandidatesCreate"]) =>
      api.post<Generation[]>(`/books/${bookId}/character/candidates`, b),
    onSuccess: () => invalidateJobs(qc, bookId),
  });
}

/** invalidate: book, books, generations */
export function useSetCharacter(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (generationId: number) =>
      api.post<Book>(`/books/${bookId}/character`, { generation_id: generationId }),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
        qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
      ]),
  });
}

/** invalidate: pages, book, books */
export function useCreatePage(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: S["PageCreate"] = { scene_prompt: "" }) =>
      api.post<Page>(`/books/${bookId}/pages`, b),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
      ]),
  });
}

/** invalidate: pages, qa(page)（線寬／細節會讓線稿重算）, edits, book */
export function useUpdatePage(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, ...body }: S["PageUpdate"] & { pageId: number }) =>
      api.patch<Page>(`/pages/${pageId}`, body),
    onSuccess: (_d, v) => invalidatePage(qc, bookId, v.pageId),
  });
}

/** invalidate: pages, book, books, exportSummary；移除該頁的 qa／edits 快取 */
export function useDeletePage(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pageId: number) => api.del(`/pages/${pageId}`),
    onSuccess: (_d, pageId) => {
      qc.removeQueries({ queryKey: qk.qa(pageId) });
      qc.removeQueries({ queryKey: qk.edits(pageId) });
      return Promise.all([
        qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
        qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
        qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) }),
      ]);
    },
  });
}

/** 回傳值直接寫入 pages 快取，再 invalidate pages, exportSummary */
export function useReorderPages(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pageIds: number[]) =>
      api.post<Page[]>(`/books/${bookId}/pages/order`, { page_ids: pageIds }),
    onSuccess: (pages) => {
      qc.setQueryData(qk.pages(bookId), pages);
      return qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) });
    },
  });
}

/** invalidate: generations, queue */
export function usePageCandidates(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, ...body }: S["PageCandidatesCreate"] & { pageId: number }) =>
      api.post<Generation[]>(`/pages/${pageId}/candidates`, body),
    onSuccess: () => invalidateJobs(qc, bookId),
  });
}

/** select → pages, qa, edits, book, books, exportSummary */
export function useSelectGeneration(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, generationId }: { pageId: number; generationId: number }) =>
      api.post<Page>(`/pages/${pageId}/select`, { generation_id: generationId }),
    onSuccess: (_d, v) => invalidatePage(qc, bookId, v.pageId),
  });
}

/** discard → generations；若捨棄的是已選圖，頁面也可能被清除選擇，所以一併 invalidate pages */
export function useUpdateGeneration(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, discarded }: { id: number; discarded: boolean }) =>
      api.patch<Generation>(`/generations/${id}`, { discarded }),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
        qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
      ]),
  });
}

/** invalidate: generations, queue */
export function useCancelGeneration(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<Generation>(`/generations/${id}/cancel`),
    onSuccess: () => invalidateJobs(qc, bookId),
  });
}

/** upload → pages（含 sketch_url；草圖只影響之後的生成） */
export function useUploadSketch(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, file }: { pageId: number; file: Blob }) =>
      api.upload<Page>(`/pages/${pageId}/sketch`, file, "PUT"),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
  });
}

/** invalidate: pages */
export function useDeleteSketch(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pageId: number) => api.del<Page>(`/pages/${pageId}/sketch`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
  });
}

/** autofix → qa, edits, pages, book, exportSummary */
export function useAutofix(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, generationId }: { pageId: number; generationId: number }) =>
      api.post<S["AutofixOut"]>(`/pages/${pageId}/lineart/autofix`, { generation_id: generationId }),
    onSuccess: (_d, v) => invalidatePage(qc, bookId, v.pageId),
  });
}

/** character-check（帶目前 selected_generation_id）→ pages, qa, book */
export function useCharacterCheck(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, ...body }: S["CharacterCheckIn"] & { pageId: number }) =>
      api.post<Page>(`/pages/${pageId}/character-check`, body),
    onSuccess: (_d, v) => invalidatePage(qc, bookId, v.pageId),
  });
}

/** pass → pages, qa, book, books, exportSummary */
export function usePass(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, forceReason }: { pageId: number; forceReason?: string | null }) =>
      api.post<Page>(`/pages/${pageId}/pass`, { force_reason: forceReason ?? null }),
    onSuccess: (_d, v) => invalidatePage(qc, bookId, v.pageId),
  });
}

/** unpass → 同 pass */
export function useUnpass(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pageId: number) => api.post<Page>(`/pages/${pageId}/unpass`),
    onSuccess: (_d, pageId) => invalidatePage(qc, bookId, pageId),
  });
}
