/**
 * 「貼上整本故事」的匯入規則（原本在 ImportDialog）：只填空欄位、補不足的頁數（被跳過的中間段落補空白頁，
 * 維持頁序對應）、有問題的段落整段不套用、從不刪除頁面。A 的「開始做這本書」第一步用它。
 */

import type { Placement } from "@/api/client";
import type { ImportRow } from "@/lib/storyImport";
import type { LineDetail, PackCleanup } from "@/lib/themePacks";

export interface PagePatch {
  pageId: number;
  scene_prompt?: string;
  action_prompt?: string;
  caption_zh?: string;
  caption_en?: string;
  line_detail?: LineDetail;
  auto_cleanup?: boolean;
  cleanup_prompt?: string | null;
}

export interface ImportOptions {
  /** 主題包的書：建立的頁與填了內容的頁一起設定線條多寡。 */
  lineDetail?: LineDetail;
  /** 主題包的每頁主角位置（第 n 頁在 n-1）：建立的頁與填了內容的頁存成位置草稿。 */
  placements?: readonly (Placement | undefined)[];
  /** 主題包的每頁背景清理設定（第 n 頁在 n-1）：建立的頁與填了內容的頁存 auto_cleanup、cleanup_prompt
   * （和場景同一個 PATCH：後端改場景會關掉自動清理，同一個請求明確帶的值優先）。 */
  cleanups?: readonly (PackCleanup | undefined)[];
}

export interface ImportCalls {
  createPage: (b: { scene_prompt: string; action_prompt: string }) => Promise<{ id: number }>;
  updatePage: (b: PagePatch) => Promise<unknown>;
  /** 有 placements 時才會呼叫。 */
  savePlacement?: (pageId: number, p: Placement) => Promise<unknown>;
}

const cells = (r: ImportRow) => +r.fill.scene + +r.fill.action + +r.fill.captionZh + +r.fill.captionEn;

/** 這一列會套用到頁面（建立新頁，或現有頁有空欄位可以填）：線條多寡、位置、清理設定也只套用到這些頁。 */
export const appliesTo = (r: ImportRow) => r.problems.length === 0 && (r.create || cells(r) > 0);

/** 第 n 頁要送的清理欄位（沒有設定就不送）。 */
function cleanupFields(cleanups: ImportOptions["cleanups"], n: number): Pick<PagePatch, "auto_cleanup" | "cleanup_prompt"> {
  const c = cleanups?.[n - 1];
  return c ? { auto_cleanup: c.auto, cleanup_prompt: c.prompt ?? null } : {};
}

/**
 * existing：已依 position 排序的現有頁面。依序建立、再補文字；回傳匯入後每一頁的 id（依頁序，第 n 頁在 n-1）。
 * 呼叫中途失敗時丟出錯誤（已建立的頁留著；再按一次時它們是現有頁，只會填空欄位，不會重複建立）。
 */
export async function applyImport(
  rows: readonly ImportRow[],
  existing: readonly { id: number }[],
  calls: ImportCalls,
  opts: ImportOptions = {},
): Promise<number[]> {
  const detail = opts.lineDetail ? { line_detail: opts.lineDetail } : {};
  const ids = existing.map((p) => p.id);
  const place = async (pageId: number, n: number) => {
    const p = opts.placements?.[n - 1];
    if (p && calls.savePlacement) await calls.savePlacement(pageId, p);
  };
  const ok = rows.filter((r) => r.problems.length === 0);
  const byN = new Map(ok.map((r) => [r.n, r]));
  const last = Math.max(existing.length, ...ok.filter((r) => r.create).map((r) => r.n));

  // 1. 依序補頁；新增頁面的建立請求沒有文字欄位：建好之後再補
  for (let n = existing.length + 1; n <= last; n++) {
    const r = byN.get(n);
    const made = await calls.createPage({ scene_prompt: r?.scene ?? "", action_prompt: r?.action ?? "" });
    ids.push(made.id);
    const cleanup = cleanupFields(opts.cleanups, n);
    if (r && (r.fill.captionZh || r.fill.captionEn || opts.lineDetail || "auto_cleanup" in cleanup)) {
      await calls.updatePage({
        pageId: made.id,
        ...(r.fill.captionZh ? { caption_zh: r.captionZh } : {}),
        ...(r.fill.captionEn ? { caption_en: r.captionEn } : {}),
        ...detail,
        ...cleanup,
      });
    }
    if (r) await place(made.id, n);
  }
  // 2. 現有頁只填空欄位
  for (const r of ok) {
    if (r.create || !appliesTo(r)) continue;
    await calls.updatePage({
      pageId: existing[r.n - 1].id,
      ...(r.fill.scene ? { scene_prompt: r.scene } : {}),
      ...(r.fill.action ? { action_prompt: r.action } : {}),
      ...(r.fill.captionZh ? { caption_zh: r.captionZh } : {}),
      ...(r.fill.captionEn ? { caption_en: r.captionEn } : {}),
      ...detail,
      ...cleanupFields(opts.cleanups, r.n),
    });
    await place(existing[r.n - 1].id, r.n);
  }
  return ids;
}
