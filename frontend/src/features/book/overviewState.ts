import type { BookOverview, OverviewPage } from "@/api/client";
import { overviewActive } from "@/api/hooks";

export type PageState = OverviewPage["state"];
export type Filter = "all" | "attention" | "ready" | "unfinished";

export const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "attention", label: "請看一下" },
  { value: "ready", label: "可以印" },
  { value: "unfinished", label: "還沒做完" },
];

/** 頁面卡的狀態標籤（spec §4.3）。 */
export const STATE_LABEL: Record<PageState, string> = {
  ready: "可以印 ✓",
  attention: "請看一下",
  working: "製作中",
  todo: "還沒做完",
};

/**
 * 要請你看一下的頁：真的有問題（attention），或是批次重畫後換了新的主角動作（review，軟性的：
 * 狀態還是 ready，同時算在「可以印」）。review 只在 ready 時算：又在製作中的頁先顯示製作中。
 */
export const inAttention = (p: OverviewPage): boolean => p.state === "attention" || (p.review && p.state === "ready");

const inFilter = (f: Filter, p: OverviewPage) =>
  f === "all" ||
  (f === "unfinished" ? p.state === "working" || p.state === "todo" : f === "attention" ? inAttention(p) : p.state === f);

export const filterPages = (pages: OverviewPage[], f: Filter) => pages.filter((p) => inFilter(f, p));

export function countPages(pages: OverviewPage[]): Record<Filter, number> {
  const out: Record<Filter, number> = { all: 0, attention: 0, ready: 0, unfinished: 0 };
  for (const f of FILTERS) out[f.value] = filterPages(pages, f.value).length;
  return out;
}

/**
 * 狀態卡顯示哪一種：
 * - running：有排隊中／執行中的工作；
 * - redraw：主角與故事改過、還有頁面要重畫（按「繼續做」重畫）；
 * - notStarted：還沒按過「開始做這本書」，又有頁面缺素材（或還沒有頁面）；
 * - deferred：按過，還有缺素材的頁（今天的次數不夠）→「還有 N 頁明天繼續」；
 * - unfinished：沒有缺素材，但還有頁面沒做完（例如流程還沒跑、故事沒寫）；
 * - done：每頁不是可以印就是請看一下。
 */
export type CardMode = "running" | "redraw" | "notStarted" | "deferred" | "unfinished" | "done";

export function cardMode(o: BookOverview): CardMode {
  if (overviewActive(o)) return "running";
  if (o.redraw_count > 0) return "redraw";
  if (o.pages.length === 0) return "notStarted";
  if (o.deferred_count > 0) return o.run_started_at == null ? "notStarted" : "deferred";
  if (o.pages.some((p) => p.state === "todo")) return "unfinished";
  return "done";
}

/** 卡片下方的小字：失敗原因、「請看一下：換了新的主角動作」，或「N 處線沒接起來（可以不管）」。 */
export function softNote(p: OverviewPage): string | null {
  if (p.state === "attention") return p.attention_detail || "這一頁沒做好，點進去看看";
  if (p.review && p.state === "ready" && p.attention_detail) return p.attention_detail;
  if (p.gap_count) return `${p.gap_count} 處線沒接起來（可以不管）`;
  return null;
}

/** 整本的進度（頁）：可以印的頁／全部的頁（後端算好的 progress_pages）。 */
export function progressPages(o: BookOverview): { ready: number; total: number } {
  return o.progress_pages;
}

/**
 * 「2 頁可以印・1 頁請你看一下・1 頁還沒做完」（0 的不寫）。每頁只算一次：換了新動作的 review 頁算可以印
 * （篩選的「請看一下」仍然包含它），所以加起來等於頁數。
 */
export function countsLine(pages: OverviewPage[]): string {
  const c = {
    ready: pages.filter((p) => p.state === "ready").length,
    attention: pages.filter((p) => p.state === "attention").length,
    unfinished: filterPages(pages, "unfinished").length,
  };
  const parts = [
    c.ready > 0 && `${c.ready} 頁可以印`,
    c.attention > 0 && `${c.attention} 頁請你看一下`,
    c.unfinished > 0 && `${c.unfinished} 頁還沒做完`,
  ].filter(Boolean);
  return parts.length ? parts.join("・") : "還沒有頁面";
}
