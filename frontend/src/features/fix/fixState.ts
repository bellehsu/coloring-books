import type { OverviewPage, Page } from "@/api/client";
import { normalizeCaption } from "@/lib/limits";
import { hardAttention } from "@/lib/pageState";
import { inAttention, type PageState } from "@/features/book/overviewState";
import type { FixFilter } from "@/lib/fixHref";


export type RestoreItem = "background" | "pose" | "lines";

/**
 * 「↺ 還原成自動畫的那張」：背景／角色動作選的不是自動畫的那張（auto_* 有值、而且不同）；
 * 線條有使用者自己畫／擦的線。
 */
export function showRestore(page: Page, item: RestoreItem): boolean {
  if (item === "lines") return page.has_user_strokes;
  const [selected, auto] =
    item === "background"
      ? [page.background_generation_id, page.auto_background_generation_id]
      : [page.pose_generation_id, page.auto_pose_generation_id];
  return selected != null && auto != null && selected !== auto;
}

export const RECOMPOSE_PENDING = "這一頁正在重新合成，請稍候再畫";
export const RECOMPOSE_FAILED = "這一頁合成失敗，請先按「再試一次」";

/** 畫筆／擦除不能用的原因（等著重新合成時後端會拒絕筆畫）；可以畫時 null。 */
export function penBlocked(page: Page): string | null {
  if (!page.recompose_pending) return null;
  return page.attention === "failed" ? RECOMPOSE_FAILED : RECOMPOSE_PENDING;
}

/**
 * 上一頁／下一頁（依頁序）。filter="attention"：只在「請看一下」的頁（含換了新動作的 review 頁）之間跳
 * （目前這頁不論狀態都當成起點）；
 * 其他：全部的頁。沒有就是 null。
 */
export function neighbours(
  pages: readonly OverviewPage[],
  pageId: number,
  filter: FixFilter,
): { prev: OverviewPage | null; next: OverviewPage | null } {
  const sorted = [...pages].sort((a, b) => a.position - b.position || a.id - b.id);
  const i = sorted.findIndex((p) => p.id === pageId);
  if (i < 0) return { prev: null, next: null };
  const ok = (p: OverviewPage) => !filter || inAttention(p);
  const prev = sorted.slice(0, i).reverse().find(ok) ?? null;
  const next = sorted.slice(i + 1).find(ok) ?? null;
  return { prev, next };
}

/** 頁首標題：「第 1 頁・消防車」（沒有圖下方的中文字就只有頁碼）。 */
export function pageTitle(page: Pick<Page, "position" | "caption_zh">): string {
  const zh = normalizeCaption(page.caption_zh ?? "");
  return zh ? `第 ${page.position + 1} 頁・${zh}` : `第 ${page.position + 1} 頁`;
}

/** 頁首的狀態：以 B 的總覽為準（含製作中）；總覽還沒載入時由頁面推（review_pose 是軟性的，仍算可以印）。 */
export function pageState(page: Page, ov: OverviewPage | undefined): PageState {
  if (ov) return ov.state;
  if (hardAttention(page)) return "attention";
  return page.status === "passed" ? "ready" : "todo";
}

type CaptionFields = "caption_zh" | "caption_en" | "caption_show_zh" | "caption_show_en";

export const CAPTION_BLANK = "不印字・圖下方留白";

/** 要印這一行嗎（沒有這個欄位的舊資料＝印，和後端的預設一樣）。 */
const shows = (v: boolean | undefined) => v !== false;

/** 真的會印出來的字（開著而且有字的行）；兩個都關是 "blank"（圖下方留一條空白給小朋友寫）。 */
export function printedCaptions(page: Pick<Page, CaptionFields>): { zh: string; en: string } | "blank" {
  const zhOn = shows(page.caption_show_zh);
  const enOn = shows(page.caption_show_en);
  if (!zhOn && !enOn) return "blank";
  return {
    zh: zhOn ? normalizeCaption(page.caption_zh ?? "") : "",
    en: enOn ? normalizeCaption(page.caption_en ?? "") : "",
  };
}

/** 圖下方的字（一行摘要，只列會印的行）：「消防車・Fire Engine」；兩個都關：「不印字・圖下方留白」。 */
export function captionSummary(page: Pick<Page, CaptionFields>): string {
  const printed = printedCaptions(page);
  if (printed === "blank") return CAPTION_BLANK;
  const parts = [printed.zh, printed.en].filter(Boolean);
  return parts.length ? parts.join("・") : "沒有字";
}

/** 「套用到整本書」成功時的說法。 */
export function captionChoiceLabel(showZh: boolean, showEn: boolean): string {
  if (showZh && showEn) return "中文和英文都印";
  if (showZh) return "只印中文";
  if (showEn) return "只印英文";
  return "圖下方留白";
}

/** 列印頁預覽網址：線稿 key（lineart_url 的 ?v=）加上文字與要印哪幾行，任何一個改了就重新載入。 */
export function printUrl(page: Pick<Page, "id" | "lineart_url" | CaptionFields>): string {
  const key = page.lineart_url?.split("?v=")[1] ?? "";
  const show = `${shows(page.caption_show_zh) ? 1 : 0}${shows(page.caption_show_en) ? 1 : 0}`;
  const v = `${key}|${page.caption_zh ?? ""}|${page.caption_en ?? ""}|${show}`;
  return `/api/pages/${page.id}/print.png?v=${encodeURIComponent(v)}`;
}
