import type { BatchBackgrounds, Generation, Page } from "@/api/client";

/** 「產生所有背景」略過的原因（同後端 POST /books/{id}/backgrounds/batch）。 */
export const SKIP_NO_SCENE = "沒有場景描述";
export const SKIP_HAS_BACKGROUND = "已有背景";
export const SKIP_GENERATING = "背景產生中";
const PREDICTED = new Set([SKIP_NO_SCENE, SKIP_HAS_BACKGROUND, SKIP_GENERATING]);

export interface BatchSkipItem {
  pageId: number;
  /** 頁碼（頁面清單順序，從 1 起）。 */
  n: number;
  reason: string;
}

export interface BatchPlan {
  /** 會排入背景的頁 id（頁面清單順序）。 */
  queued: number[];
  skipped: BatchSkipItem[];
  /** 生成紀錄還沒載入：只用頁面欄位判斷，「已有背景」「背景產生中」可能漏算。 */
  estimate: boolean;
}

/**
 * 前端預估「產生所有背景」會排入哪些頁：與後端同樣三個條件、同樣順序。
 * gens 是整本書未捨棄的生成紀錄（EditorPage 已載入的 useGenerations(bookId)）；
 * 後端另外會略過草圖檔案遺失的頁，前端無法預知，實際結果以回應為準。
 */
export function planBatchBackgrounds(pages: readonly Page[], gens: readonly Generation[] | undefined): BatchPlan {
  const queued: number[] = [];
  const skipped: BatchSkipItem[] = [];
  pages.forEach((p, i) => {
    const bgs = (gens ?? []).filter((g) => g.kind === "background" && g.page_id === p.id);
    let reason: string | null = null;
    if (!p.scene_prompt.trim()) reason = SKIP_NO_SCENE;
    else if (p.background_generation_id != null || bgs.some((g) => g.status === "succeeded" && !g.discarded))
      reason = SKIP_HAS_BACKGROUND;
    else if (bgs.some((g) => g.status === "queued" || g.status === "running")) reason = SKIP_GENERATING;
    if (reason) skipped.push({ pageId: p.id, n: i + 1, reason });
    else queued.push(p.id);
  });
  return { queued, skipped, estimate: gens === undefined };
}

const listSkips = (items: readonly { n: number; reason: string }[]) =>
  items.map((s) => `第 ${s.n} 頁${s.reason}`).join("、");

/** 確認對話框：「會為 N 頁產生背景（M 頁略過：第 x 頁沒有場景描述、…）」。 */
export function batchConfirmText(plan: BatchPlan): string {
  const head = `會為 ${plan.queued.length} 頁產生背景`;
  return plan.skipped.length ? `${head}（${plan.skipped.length} 頁略過：${listSkips(plan.skipped)}）` : head;
}

/**
 * 完成後的提示（依後端回應）：「已排入 N 頁背景、略過 M 頁」。
 * 前端預估不到的原因（草圖檔案遺失）另外列出頁碼，使用者才知道要處理哪一頁。
 */
export function batchResultText(res: BatchBackgrounds, pages: readonly Page[]): string {
  let text = `已排入 ${res.queued.length} 頁背景`;
  if (res.skipped.length) text += `、略過 ${res.skipped.length} 頁`;
  const surprises = res.skipped
    .filter((s) => !PREDICTED.has(s.reason))
    .map((s) => {
      const i = pages.findIndex((p) => p.id === s.page_id);
      return { n: i >= 0 ? i + 1 : s.position + 1, reason: s.reason };
    });
  if (surprises.length) text += `（${listSkips(surprises)}）`;
  return text;
}
