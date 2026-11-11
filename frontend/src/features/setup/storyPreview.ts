/**
 * A 做一本新的著色書（spec §4.5）的故事預覽與「會畫幾張圖」：純函式，無 I/O。
 *
 * 預覽＝這本書現有的頁＋貼上的故事（照「貼上整本故事」的規則：第 n 段對應第 n 頁、只填空欄位、補不足的頁、
 * 有問題的段落整段不套用）。張數在前端算（頁面還沒匯入，後端的 /produce/plan 看不到）：
 * 背景＝有場景、還沒有背景、沒選上傳圖片的頁；動作＝有動作、還沒有角色動作的頁。規則同後端 _BookState.skip_reason。
 * /produce/plan 只提供今天還剩幾次（remaining）與每張的秒數（estimate_s / fits）。
 */

import type { Generation, Page, ProducePlan } from "@/api/client";
import { parseStory, planImport, type ImportRow } from "@/lib/storyImport";

/** 後端 SECONDS_PER_IMAGE（api/produce.py）：/produce/plan 沒有可以換算的數字時用。 */
export const SECONDS_PER_IMAGE = 22;

/** 已有頁面的素材狀態（同後端的略過規則：已選、舊流程整頁圖、有可用的成功紀錄，或正在產生）。 */
export interface PageMaterials {
  hasBackground: boolean;
  /** 選用的背景是使用者上傳的圖片。 */
  backgroundUploaded: boolean;
  hasPose: boolean;
}

export interface PreviewEntry {
  /** 第幾頁（1 起算）。 */
  n: number;
  /** 已有的頁；按「開始做這本書」才會建立的頁為 null。 */
  pageId: number | null;
  scene: string;
  action: string;
  captionZh: string;
  captionEn: string;
  /** 已有的頁的素材；新的頁都沒有。 */
  materials: PageMaterials;
}

/** 貼上的段落裡不會套用的：看不懂、或超過字數。 */
export interface PreviewProblem {
  /** 頁碼；開場白、結語等沒有頁碼的為 null。 */
  n: number | null;
  text: string;
}

export interface StoryPreview {
  entries: PreviewEntry[];
  /** 按「開始做這本書」時交給 applyImport 的列（含有問題的列，applyImport 會略過）。 */
  rows: ImportRow[];
  /** 貼上的文字讀到幾段（含看不懂的）。 */
  pasted: number;
  problems: PreviewProblem[];
}

export type ExistingPage = Pick<
  Page,
  "id" | "position" | "scene_prompt" | "action_prompt" | "caption_zh" | "caption_en"
>;

const NONE: PageMaterials = { hasBackground: false, backgroundUploaded: false, hasPose: false };
const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

/** 每個已有頁面的素材狀態（gens：這本書的生成紀錄，任何種類）。 */
export function pageMaterials(
  pages: readonly Pick<Page, "id" | "background_generation_id" | "pose_generation_id" | "selected_generation_id">[],
  gens: readonly Generation[],
): Map<number, PageMaterials> {
  const legacy = new Set(gens.filter((g) => g.kind === "page").map((g) => g.id));
  const out = new Map<number, PageMaterials>();
  for (const p of pages) {
    const mine = gens.filter((g) => g.page_id === p.id && !g.discarded);
    const has = (kind: "background" | "pose", slot: number | null) =>
      slot != null ||
      (p.selected_generation_id != null && legacy.has(p.selected_generation_id)) ||
      mine.some((g) => g.kind === kind && (g.status === "succeeded" || isActive(g)));
    const selectedBg = gens.find((g) => g.id === p.background_generation_id);
    out.set(p.id, {
      hasBackground: has("background", p.background_generation_id),
      backgroundUploaded: selectedBg?.source === "upload",
      hasPose: has("pose", p.pose_generation_id),
    });
  }
  return out;
}

/** existing 依 position 排序後，第 n 段對應第 n 頁。 */
export function buildPreview(
  existing: readonly ExistingPage[],
  text: string,
  materials: ReadonlyMap<number, PageMaterials> = new Map(),
): StoryPreview {
  const pages = [...existing].sort((a, b) => a.position - b.position || a.id - b.id);
  const parsed = parseStory(text);
  const rows = planImport(parsed.pages, pages);
  const ok = new Map(rows.filter((r) => r.problems.length === 0).map((r) => [r.n, r]));
  const last = Math.max(pages.length, ...[...ok.values()].filter((r) => r.create).map((r) => r.n));

  const entries: PreviewEntry[] = [];
  for (let n = 1; n <= last; n++) {
    const e = pages[n - 1];
    const r = ok.get(n);
    entries.push({
      n,
      pageId: e?.id ?? null,
      scene: r?.fill.scene ? r.scene : (e?.scene_prompt ?? ""),
      action: r?.fill.action ? r.action : (e?.action_prompt ?? ""),
      captionZh: r?.fill.captionZh ? r.captionZh : (e?.caption_zh ?? ""),
      captionEn: r?.fill.captionEn ? r.captionEn : (e?.caption_en ?? ""),
      materials: (e && materials.get(e.id)) || NONE,
    });
  }

  const problems: PreviewProblem[] = [
    ...rows.filter((r) => r.problems.length > 0).map((r) => ({ n: r.n, text: r.problems.join("；") })),
    ...parsed.errors.map((er) => ({ n: er.n, text: `${er.reason}：${er.excerpt}` })),
  ].sort((a, b) => (a.n ?? 1e9) - (b.n ?? 1e9));

  return { entries, rows, pasted: parsed.pages.length + parsed.errors.length, problems };
}

/** 這一頁按下開始後要不要畫背景（uploads：選了上傳圖片的頁碼）。 */
export const needsBackground = (e: PreviewEntry, uploads: ReadonlySet<number>) =>
  e.scene.trim() !== "" && !e.materials.hasBackground && !uploads.has(e.n);
export const needsPose = (e: PreviewEntry) => e.action.trim() !== "" && !e.materials.hasPose;

export interface PlanCounts {
  backgrounds: number;
  poses: number;
  total: number;
  /** 今天放得下幾張（remaining 不限時＝total）。 */
  fits: number;
  /** 今天還剩幾次；null＝不限（管理者）；undefined＝還不知道（書還沒建立）。 */
  remaining: number | null | undefined;
  /** 今天這些大約要幾分鐘（至少 1）。 */
  minutes: number;
}

export function planCounts(
  entries: readonly PreviewEntry[],
  uploads: ReadonlySet<number>,
  plan?: Pick<ProducePlan, "remaining" | "estimate_s" | "fits">,
): PlanCounts {
  const backgrounds = entries.filter((e) => needsBackground(e, uploads)).length;
  const poses = entries.filter(needsPose).length;
  const total = backgrounds + poses;
  const remaining = plan ? plan.remaining : undefined;
  const fits = remaining == null ? total : Math.min(total, remaining);
  const perImage = plan && plan.fits > 0 ? plan.estimate_s / plan.fits : SECONDS_PER_IMAGE;
  const minutes = fits > 0 ? Math.max(1, Math.ceil((fits * perImage) / 60)) : 0;
  return { backgrounds, poses, total, fits, remaining, minutes };
}

export const NO_QUOTA = "今天的生成次數已用完，明天再來";

/** 底部列的文字：標題、說明，以及今天放不下時的「今天先做 X 張，其餘明天繼續」。 */
export function planText(c: PlanCounts, ready: boolean): { heading: string; detail: string; defer: string | null } {
  const prefix = ready ? "準備好了！" : "";
  if (c.total === 0) {
    return {
      heading: `${prefix}不用再畫新的圖`,
      detail: ready ? "按下去就會把每一頁合成、補好線。" : "",
      defer: null,
    };
  }
  const heading = `${prefix}會畫 ${c.total} 張圖（背景 ${c.backgrounds}＋動作 ${c.poses}）`;
  if (c.fits === 0) return { heading, detail: NO_QUOTA, defer: null };
  const cap = c.remaining == null ? "" : `（今天會用 ${c.fits} 次，還剩 ${c.remaining} 次）`;
  return {
    heading,
    detail: `大約 ${c.minutes} 分鐘。可以先去忙，好了會通知你${cap}。`,
    defer: c.fits < c.total ? `今天先做 ${c.fits} 張，其餘明天繼續` : null,
  };
}
