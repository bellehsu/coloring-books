import { useEffect, useRef } from "react";
import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import { placementSaverFor } from "@/features/editing/placementSaver";
import { recomposeRunning } from "@/lib/pageState";
import type { components } from "./schema";
import type {
  Book,
  BookOverview,
  Edits,
  ExportSummary,
  Generation,
  Invite,
  InviteCreate,
  Me,
  Page,
  ProducePlan,
  ProduceResult,
  QA,
  SharedBook,
  SharedBookDetail,
  QueueStatus,
} from "./client";

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
  overview: (bookId: number) => ["overview", bookId] as const,
  producePlan: (bookId: number) => ["producePlan", bookId] as const,
  sharedBooks: () => ["sharedBooks"] as const,
  sharedBook: (id: number) => ["sharedBook", id] as const,
  me: () => ["me"] as const,
  invites: () => ["invites"] as const,
};

export interface GenFilter {
  kind?: "character" | "page" | "background" | "pose" | "composite";
  pageId?: number;
  includeDiscarded?: boolean;
}

/** 生成還在排隊或執行中。 */
export const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

// ---------- queries ----------

export const useBooks = () => useQuery({ queryKey: qk.books(), queryFn: () => api.get<Book[]>("/books") });

/** enabled＝false：還沒有書（A 的 /books/new 在書建立前）。 */
export const useBook = (id: number, enabled = true) =>
  useQuery({ queryKey: qk.book(id), queryFn: () => api.get<Book>(`/books/${id}`), enabled });

/** 目前的使用者（名字與是否為管理者）；首頁用它決定要不要顯示「邀請管理」。 */
export const useMe = () => useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me"), retry: false });

/** 管理者的邀請列表；非管理者後端回 404（頁面顯示一般的 404）。 */
export const useInvites = () =>
  useQuery({ queryKey: qk.invites(), queryFn: () => api.get<Invite[]>("/admin/invites"), retry: false });

export const useCreateInvite = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InviteCreate) => api.post<Invite>("/admin/invites", body),
    onSuccess: (row) => {
      qc.setQueryData<Invite[]>(qk.invites(), (old) => [row, ...(old ?? []).filter((r) => r.id !== row.id)]);
      void qc.invalidateQueries({ queryKey: qk.invites() });
    },
  });
};

/** 別人分享的書（唯讀）。 */
export const useSharedBooks = () =>
  useQuery({ queryKey: qk.sharedBooks(), queryFn: () => api.get<SharedBook[]>("/shared/books") });

/** 單本分享的書（只含完成的頁面）；404＝已不再分享，不重試。 */
export const useSharedBook = (id: number) =>
  useQuery({
    queryKey: qk.sharedBook(id),
    queryFn: () => api.get<SharedBookDetail>(`/shared/books/${id}`),
    retry: false,
  });

/** 這本書的頁面查詢（usePages、進階編輯跟著重新合成的觀察者、C 的 useFixPages 共用；各自加輪詢等選項）。 */
export const pagesQueryOptions = (bookId: number) =>
  queryOptions({ queryKey: qk.pages(bookId), queryFn: () => api.get<Page[]>(`/books/${bookId}/pages`) });

export const usePages = (bookId: number, enabled = true) => useQuery({ ...pagesQueryOptions(bookId), enabled });

/** 有 queued/running 的 generation 時每 2 秒輪詢，否則停止。 */
export function useGenerations(bookId: number, f: GenFilter = {}, enabled = true) {
  return useQuery({
    enabled,
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

/** 頁面等著重新合成時重抓頁面的間隔。 */
const RECOMPOSE_POLL_MS = 2000;

/**
 * 任務結束時 worker 可能把它自動選用到頁面的空欄位（背景、角色動作），但 pages 不會自己重抓：
 * gens（useGenerations 的資料）裡原本排隊／執行中的任務結束了 → invalidate pages 與 book。
 * C（處理一頁）與進階編輯都用，畫面不用重新整理就會看到自動選用的素材。
 * pollPageId（編輯器的目前頁）：
 * - 分頁切回來（visibilitychange 變成可見）或視窗取得 focus 就重抓頁面：別的分頁（C）可能改過這頁；
 * - 這頁還在重新合成（recomposeRunning）時每 2 秒重抓頁面，直到做完或失敗；結束那一刻重抓這頁的
 *   生成紀錄、QA 與修線（選定圖換成新的合成圖）。
 */
export function usePagesFollowJobs(
  bookId: number,
  gens: readonly Generation[] | undefined,
  pollPageId: number | null = null,
) {
  const qc = useQueryClient();
  const active = useRef<Set<number> | null>(null);
  useEffect(() => {
    if (!gens) return;
    const now = new Set(gens.filter(isActive).map((g) => g.id));
    const before = active.current;
    active.current = now;
    if (before && [...before].some((id) => !now.has(id))) {
      void qc.invalidateQueries({ queryKey: qk.pages(bookId) });
      void qc.invalidateQueries({ queryKey: qk.book(bookId) });
    }
  }, [gens, qc, bookId]);

  const recomposing = (pages: Page[] | undefined) => recomposeRunning(pages?.find((p) => p.id === pollPageId));
  const pagesQ = useQuery({
    ...pagesQueryOptions(bookId),
    enabled: pollPageId != null,
    // 只有這個觀察者（全域預設關掉）
    refetchOnWindowFocus: true,
    refetchInterval: (query) => (recomposing(query.state.data) ? RECOMPOSE_POLL_MS : false),
  });
  const watching = pollPageId != null;
  const { refetch } = pagesQ;
  useEffect(() => {
    if (!watching) return;
    const onVisible = () => {
      if (document.visibilityState !== "hidden") void refetch({ cancelRefetch: false });
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [watching, refetch]);
  const pending = pollPageId != null && recomposing(pagesQ.data);
  const was = useRef(pending);
  useEffect(() => {
    if (was.current && !pending && pollPageId != null) {
      void qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) });
      void qc.invalidateQueries({ queryKey: qk.qa(pollPageId) });
      void qc.invalidateQueries({ queryKey: qk.edits(pollPageId) });
      void qc.invalidateQueries({ queryKey: qk.book(bookId) });
    }
    was.current = pending;
  }, [pending, pollPageId, qc, bookId]);
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

/** 書還在做（有排隊中／執行中的工作，或有頁面製作中）。 */
export const overviewActive = (o: BookOverview) =>
  o.progress.queued + o.progress.running > 0 || o.pages.some((p) => p.state === "working");

/**
 * 整本書總覽：還在做時每 3 秒輪詢，做完就停。background（只有 B 傳 true）：分頁在背景時也照樣輪詢
 * （只在還在做時）——「可以先去忙，好了會通知你」的分頁標題「✓ 做好了」就是給在別的分頁的人看的；
 * C 沒有分頁標題通知，在背景不輪詢。
 */
export const OVERVIEW_POLL_MS = 3000;
export const useBookOverview = (bookId: number, { background = false }: { background?: boolean } = {}) =>
  useQuery({
    queryKey: qk.overview(bookId),
    queryFn: () => api.get<BookOverview>(`/books/${bookId}/overview`),
    refetchInterval: (query) => (query.state.data && overviewActive(query.state.data) ? OVERVIEW_POLL_MS : false),
    refetchIntervalInBackground: background,
  });

/**
 * 「開始做這本書」要排幾張、今天還剩幾次（remaining null＝不限）。
 * 回到這個分頁就重抓（開著過夜，隔天次數恢復「繼續做」才會亮）；今天用完（remaining 0）時每分鐘再看一次。
 */
export const PLAN_RECHECK_MS = 60_000;
export const useProducePlan = (bookId: number, enabled = true) =>
  useQuery({
    enabled,
    queryKey: qk.producePlan(bookId),
    queryFn: () => api.get<ProducePlan>(`/books/${bookId}/produce/plan`),
    refetchOnWindowFocus: true,
    refetchInterval: (query) => (query.state.data?.remaining === 0 ? PLAN_RECHECK_MS : false),
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

/** 分享開關（PUT /books/{id}/share）→ book, books, sharedBooks */
export function useShareBook(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (shared: boolean) => api.put<Book>(`/books/${bookId}/share`, { shared }),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
        qc.invalidateQueries({ queryKey: qk.sharedBooks() }),
      ]),
  });
}

/** 主角換了（選定或上傳）：book（回應直接寫進快取）, books, generations, producePlan。 */
function afterCharacterSet(qc: QueryClient, book: Book) {
  qc.setQueryData(qk.book(book.id), book);
  return Promise.all([
    qc.invalidateQueries({ queryKey: qk.book(book.id) }),
    qc.invalidateQueries({ queryKey: qk.books() }),
    qc.invalidateQueries({ queryKey: qk.generationsOf(book.id) }),
    qc.invalidateQueries({ queryKey: qk.producePlan(book.id) }),
  ]);
}

/**
 * A 的主角（spec §4.5）：bookId 放在 variables，因為 /books/new 的書是在第一次動作時才建立的。
 * - generate：產生一張候選（已有主角時要 confirm＝true，後端才不會 409；這只是多一張候選，主角還沒換）
 * - choose：選這隻（已有主角時 confirm＝true：使用者已確認「換主角？」）
 * - upload：↑ 上傳自己的主角（multipart；confirm 同上）
 */
export function useSetupCharacter() {
  const qc = useQueryClient();
  const generate = useMutation({
    mutationFn: ({ bookId, description, confirm }: { bookId: number; description: string; confirm: boolean }) =>
      api.post<Generation[]>(`/books/${bookId}/character/candidates`, {
        description,
        ...(confirm ? { confirm_change: true } : {}),
      }),
    onSuccess: (_d, v) =>
      Promise.all([invalidateJobs(qc, v.bookId), qc.invalidateQueries({ queryKey: qk.producePlan(v.bookId) })]),
  });
  const choose = useMutation({
    mutationFn: ({ bookId, generationId, confirm }: { bookId: number; generationId: number; confirm: boolean }) =>
      api.post<Book>(`/books/${bookId}/character`, {
        generation_id: generationId,
        ...(confirm ? { confirm_change: true } : {}),
      }),
    onSuccess: (book) => afterCharacterSet(qc, book),
  });
  const upload = useMutation({
    mutationFn: ({ bookId, file, confirm }: { bookId: number; file: File; confirm: boolean }) =>
      api.upload<Book>(`/books/${bookId}/character/upload`, file, confirm ? { confirm_change: "true" } : {}),
    onSuccess: (book) => afterCharacterSet(qc, book),
  });
  return { generate, choose, upload };
}

/** invalidate: pages, book, books */
export function useCreatePage(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: S["PageCreate"] = { scene_prompt: "", action_prompt: "" }) =>
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

/** autofix → qa, edits, pages, book, exportSummary。oneclick（C）：流程做的頁補了線也不退回草稿。 */
export function useAutofix(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, generationId, oneclick }: { pageId: number; generationId: number; oneclick?: boolean }) =>
      api.post<S["AutofixOut"]>(`/pages/${pageId}/lineart/autofix${oneclick ? "?oneclick=true" : ""}`, {
        generation_id: generationId,
      }),
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

/** pass（不帶原因：有缺口時後端記下「使用者確認完成（N 個缺口）」）→ pages, qa, book, books, exportSummary */
export function usePass(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId }: { pageId: number }) => api.post<Page>(`/pages/${pageId}/pass`, {} satisfies S["PassIn"]),
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

/** 背景：新增一張（排隊，Qwen，用頁面的場景描述；沒有風格可選）→ generations, queue */
export function useGenerateBackground(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId }: { pageId: number }) =>
      api.post<Generation>(`/pages/${pageId}/backgrounds`, {} satisfies S["BackgroundCreate"]),
    onSuccess: () => invalidateJobs(qc, bookId),
  });
}

/**
 * 上傳圖片當這頁的背景（後端置中裁成頁面比例並選用）：等於改選背景 → 同 useSelectBackground
 * （invalidatePage：pages, qa, edits, book, books, exportSummary），另加 generations 與 queue。
 */
export function useUploadBackground(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, file }: { pageId: number; file: File }) =>
      api.upload<Page>(`/pages/${pageId}/backgrounds/upload`, file),
    onSuccess: (_d, v) => Promise.all([invalidatePage(qc, bookId, v.pageId), invalidateJobs(qc, bookId)]),
  });
}

/** 角色動作：新增一張（排隊，Qwen 精準模式，自動去背；沒有產生方式可選）→ generations, queue */
export function useGeneratePose(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, action }: { pageId: number; action: string }) =>
      api.post<Generation>(`/pages/${pageId}/poses`, { action } satisfies S["PoseCreate"]),
    onSuccess: () => invalidateJobs(qc, bookId),
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
 * 合成（無 body；後端用已存的背景／動作／位置；編輯器的自動合成呼叫它）→ pages, qa, edits, book, books, exportSummary, generations。
 * mutationFn 內會先等 placementSaver.whenIdle(pageId)，位置寫入失敗則拒絕（「位置尚未儲存，請重試」）且不送出合成。合成圖只能經由這裡產生。
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

/**
 * 開始做這本書／繼續做：補排缺的素材與頁面流程（放得下多少做多少）。0 次可用時後端回 429。
 * → overview, producePlan, queue, generations, pages, book, books（失敗時只重抓 producePlan）
 */
export function useProduce(bookId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ProduceResult>(`/books/${bookId}/produce`),
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.overview(bookId) }),
        qc.invalidateQueries({ queryKey: qk.producePlan(bookId) }),
        qc.invalidateQueries({ queryKey: qk.pages(bookId) }),
        qc.invalidateQueries({ queryKey: qk.book(bookId) }),
        qc.invalidateQueries({ queryKey: qk.books() }),
        invalidateJobs(qc, bookId),
      ]),
    // 失敗（429）也重抓剩餘次數：「繼續做」與「今天的生成次數已用完」才會跟著變。不 await：錯誤訊息不用等它
    onError: () => {
      void qc.invalidateQueries({ queryKey: qk.producePlan(bookId) });
    },
  });
}
