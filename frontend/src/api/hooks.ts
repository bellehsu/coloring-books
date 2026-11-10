import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api, ApiError } from "./client";
import { placementSaverFor } from "@/features/editor/placementSaver";
import type { components } from "./schema";
import type {
  BackgroundStyles,
  BatchBackgrounds,
  Book,
  Edits,
  ExportSummary,
  Generation,
  Page,
  PoseModes,
  QA,
  QueueStatus,
} from "./client";
import type { BgStyle } from "@/lib/bgStyle";
import type { PoseMode } from "@/lib/poseMode";

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
  poseModes: () => ["poseModes"] as const,
  backgroundStyles: () => ["backgroundStyles"] as const,
};

export interface GenFilter {
  kind?: "character" | "page" | "background" | "pose" | "composite";
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

/** 角色動作的精準／快速是否可用（後端快取的 preflight 結果）；一分鐘內不重抓。 */
export const usePoseModes = (enabled = true) =>
  useQuery({
    queryKey: qk.poseModes(),
    queryFn: () => api.get<PoseModes>("/system/pose-modes"),
    staleTime: 60_000,
    enabled,
  });

/** 背景風格（簡潔／細緻）可不可用：後端只看快取的 preflight。 */
export const useBackgroundStyles = (enabled = true) =>
  useQuery({
    queryKey: qk.backgroundStyles(),
    queryFn: () => api.get<BackgroundStyles>("/system/background-styles"),
    staleTime: 60_000,
    enabled,
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
        // 版面改變後後端會重算線稿與 QA；QA 結果以 ["qa"] 前綴一併重抓
        qc.invalidateQueries({ queryKey: ["qa"] }),
        qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) }),
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

/** 背景：新增一張（排隊），一律帶風格（後端遇到可用草圖或簡潔不可用會改用細緻）→ generations, queue */
export function useGenerateBackground(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, style }: { pageId: number; style: BgStyle }) =>
      api.post<Generation>(`/pages/${pageId}/backgrounds`, { style } satisfies S["BackgroundCreate"]),
    onSuccess: () => invalidateJobs(qc, bookId),
  });
}

/**
 * 整本書每一頁排一張背景（後端略過沒場景描述、已有背景、背景產生中、草圖遺失的頁），一律帶風格
 * （有可用草圖的頁後端用細緻）→ generations（qk.generationsOf 前綴涵蓋每一頁的歷史與排隊數）, queue
 */
export function useBatchBackgrounds(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (style: BgStyle) =>
      api.post<BatchBackgrounds>(`/books/${bookId}/backgrounds/batch`, { style } satisfies S["BackgroundCreate"]),
    onSuccess: () => invalidateJobs(qc, bookId),
  });
}

/** 角色動作：新增一張（排隊，自動去背），一律帶產生方式 → generations, queue */
export function useGeneratePose(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, action, mode }: { pageId: number; action: string; mode: PoseMode }) =>
      api.post<Generation>(`/pages/${pageId}/poses`, { action, mode } satisfies S["PoseCreate"]),
    onSuccess: () => invalidateJobs(qc, bookId),
    // 503 = 精準模式剛變成不可用：馬上重抓可用狀態，切換元件才會退回快速
    onError: (e) => {
      if (e instanceof ApiError && e.status === 503) void qc.invalidateQueries({ queryKey: qk.poseModes() });
    },
  });
}

/** 選背景：若目前選定圖是合成圖，後端會清空選圖並退回草稿 → 同 invalidatePage */
export function useSelectBackground(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, generationId }: { pageId: number; generationId: number }) =>
      api.post<Page>(`/pages/${pageId}/background`, { generation_id: generationId }),
    onSuccess: (_d, v) =>
      Promise.all([
        invalidatePage(qc, bookId, v.pageId),
        qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
      ]),
  });
}

/** 選角色動作：同 useSelectBackground */
export function useSelectPose(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, generationId }: { pageId: number; generationId: number }) =>
      api.post<Page>(`/pages/${pageId}/pose`, { generation_id: generationId }),
    onSuccess: (_d, v) =>
      Promise.all([
        invalidatePage(qc, bookId, v.pageId),
        qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
      ]),
  });
}

/**
 * 確認合成（無 body；後端用已存的背景／動作／位置）→ pages, qa, edits, book, books, exportSummary, generations。
 * mutationFn 內會先等 placementSaver.whenIdle(pageId)，位置寫入失敗則拒絕（「位置尚未儲存，請重試」）且不送出合成。合成圖只能經由這裡產生或選回，不要對 composite 呼叫 /select。
 */
export function useCompose(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ pageId }: { pageId: number }) => {
      // 先等位置寫完；最後一次寫入失敗就不合成（否則合成結果不等於畫面上的位置）
      try {
        await placementSaverFor(qc).whenIdle(pageId);
      } catch {
        throw new Error("位置尚未儲存，請重試");
      }
      return api.post<Page>(`/pages/${pageId}/compose`);
    },
    onSuccess: (_d, v) =>
      Promise.all([
        invalidatePage(qc, bookId, v.pageId),
        qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) }),
      ]),
  });
}
