/**
 * A 做一本新的著色書（spec §4.5）的故事預覽與「會畫幾張圖」：純函式，無 I/O。
 *
 * 預覽＝這本書現有的頁＋貼上的故事（照「貼上整本故事」的規則：第 n 段對應第 n 頁、只填空欄位、補不足的頁、
 * 有問題的段落整段不套用）。張數在前端算（頁面還沒匯入，後端的 /produce/plan 看不到）：
 * 背景＝有場景、還沒有背景、沒選上傳圖片的頁；動作＝有動作、還沒有角色動作的頁。規則同後端 _BookState.skip_reason。
 * 自動清理的頁（auto_cleanup）的背景另外多算 1 次清理（同後端 /produce/plan 的 total）。
 * /produce/plan 只提供今天還剩幾次（remaining）與每張的秒數（estimate_s / fits）。
 */

import type { Generation, Page, Placement, ProducePlan } from "@/api/client";
import { isActive } from "@/api/hooks";
import { NO_QUOTA } from "@/lib/limits";
import { parseStory, planImport, type ImportRow } from "@/lib/storyImport";
import type { PackCleanup } from "@/lib/themePacks";
import { appliesTo } from "./applyImport";

/** 後端 SECONDS_PER_IMAGE（api/produce.py）：/produce/plan 沒有可以換算的數字時用。 */
export const SECONDS_PER_IMAGE = 22;

/** 已有頁面的素材狀態（同後端的略過規則：已選、舊流程整頁圖、有可用的成功紀錄，或正在產生）。 */
export interface PageMaterials {
  hasBackground: boolean;
  /** 選用的背景是使用者上傳的圖片。 */
  backgroundUploaded: boolean;
  hasPose: boolean;
  /** 設定頁改了場景／動作：「繼續做」會重畫（後端標的旗標）。 */
  redrawBackground: boolean;
  redrawPose: boolean;
  /** 這頁開著自動清理（新的 AI 背景好了再清掉框外雜物）。 */
  autoCleanup?: boolean;
}

/** 「＋ 加一頁」手動加的頁（還沒建立）：接在現有頁與貼上的頁後面。 */
export interface DraftPage {
  /** 不會變的編號：之後再貼故事、頁碼往後移，選好的上傳圖還是跟著這一頁。 */
  key: number;
  scene: string;
  action: string;
}

export interface PreviewEntry {
  /** 第幾頁（1 起算）。 */
  n: number;
  /** 選上傳圖用的鍵：已有或貼上的頁是 `p{n}`，手動加的頁是 `d{key}`。 */
  key: string;
  /** 手動加的頁的 DraftPage.key；其他頁為 null。 */
  draft: number | null;
  /** 已有的頁；按「開始做這本書」才會建立的頁為 null。 */
  pageId: number | null;
  scene: string;
  action: string;
  captionZh: string;
  captionEn: string;
  /** 已有的頁的素材；新的頁都沒有。 */
  materials: PageMaterials;
  /** 按下開始後這頁開著自動清理（背景多算 1 次）。 */
  autoCleanup: boolean;
  /** 這頁放不放主角（false＝「不放主角」：不畫角色動作、背景照改寫後的場景畫）。 */
  includeCharacter: boolean;
  /** 相框頁（主題包位置有 frame）：一定要放主角，勾選框停用。 */
  characterLocked: boolean;
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
> &
  Partial<Pick<Page, "include_character" | "placement">>;

const NONE: PageMaterials = {
  hasBackground: false,
  backgroundUploaded: false,
  hasPose: false,
  redrawBackground: false,
  redrawPose: false,
};
/** 每個已有頁面的素材狀態（gens：這本書的生成紀錄，任何種類）。 */
export function pageMaterials(
  pages: readonly (Pick<
    Page,
    "id" | "background_generation_id" | "pose_generation_id" | "selected_generation_id" | "redraw_background" | "redraw_pose"
  > &
    Partial<Pick<Page, "auto_cleanup">>)[],
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
      redrawBackground: p.redraw_background,
      redrawPose: p.redraw_pose,
      autoCleanup: p.auto_cleanup ?? false,
    });
  }
  return out;
}

/** 「不放主角」的狀態：noCharacter＝使用者勾的（entry.key → 不放主角？，沒有的用頁面原本的值）；placements＝主題包的位置（有 frame＝相框頁）。 */
export interface CharacterOptions {
  noCharacter?: ReadonlyMap<string, boolean>;
  placements?: readonly (Placement | undefined)[];
}

/**
 * existing 依 position 排序後，第 n 段對應第 n 頁。cleanups：主題包的每頁清理設定（同 applyImport 的
 * ImportOptions.cleanups；只套用到會填內容的頁）。
 */
export function buildPreview(
  existing: readonly ExistingPage[],
  text: string,
  materials: ReadonlyMap<number, PageMaterials> = new Map(),
  drafts: readonly DraftPage[] = [],
  cleanups?: readonly (PackCleanup | undefined)[],
  character: CharacterOptions = {},
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
    const m = (e && materials.get(e.id)) || NONE;
    const locked = (e ? e.placement?.frame : character.placements?.[n - 1]?.frame) != null;
    const key = `p${n}`;
    entries.push({
      n,
      key,
      draft: null,
      pageId: e?.id ?? null,
      scene: r?.fill.scene ? r.scene : (e?.scene_prompt ?? ""),
      action: r?.fill.action ? r.action : (e?.action_prompt ?? ""),
      captionZh: r?.fill.captionZh ? r.captionZh : (e?.caption_zh ?? ""),
      captionEn: r?.fill.captionEn ? r.captionEn : (e?.caption_en ?? ""),
      materials: m,
      autoCleanup: autoCleanupAfter(r, m, cleanups?.[n - 1]),
      includeCharacter: locked || !(character.noCharacter?.get(key) ?? e?.include_character === false),
      characterLocked: locked,
    });
  }

  drafts.forEach((d, i) =>
    entries.push({
      n: last + i + 1,
      key: `d${d.key}`,
      draft: d.key,
      pageId: null,
      scene: d.scene,
      action: d.action,
      captionZh: "",
      captionEn: "",
      materials: NONE,
      autoCleanup: false,
      includeCharacter: !(character.noCharacter?.get(`d${d.key}`) ?? false),
      characterLocked: false,
    }),
  );

  const problems: PreviewProblem[] = [
    ...rows.filter((r) => r.problems.length > 0).map((r) => ({ n: r.n, text: r.problems.join("；") })),
    ...parsed.errors.map((er) => ({ n: er.n, text: `${er.reason}：${er.excerpt}` })),
  ].sort((a, b) => (a.n ?? 1e9) - (b.n ?? 1e9));

  return { entries, rows, pasted: parsed.pages.length + parsed.errors.length, problems };
}

/**
 * 匯入後這頁開不開自動清理（同 applyImport 與後端 PATCH）：會填內容的列帶了主題包的設定就用它；
 * 沒帶設定但填了場景（改了場景文字）就關掉；其他照原本的。
 */
function autoCleanupAfter(r: ImportRow | undefined, m: PageMaterials, c: PackCleanup | undefined): boolean {
  if (r && appliesTo(r)) {
    if (c) return c.auto;
    if (r.fill.scene && !r.create) return false;
  }
  return m.autoCleanup ?? false;
}

/** 這一頁按下開始後要不要畫背景（uploads：選了上傳圖片的頁的 key）。 */
export const needsBackground = (e: PreviewEntry, uploads: ReadonlySet<string>) =>
  e.scene.trim() !== "" && (!e.materials.hasBackground || e.materials.redrawBackground) && !uploads.has(e.key);
export const needsPose = (e: PreviewEntry) =>
  e.includeCharacter && e.action.trim() !== "" && (!e.materials.hasPose || e.materials.redrawPose);

export interface PlanCounts {
  backgrounds: number;
  poses: number;
  /** 自動清理的頁的背景：每張另外清一次。 */
  cleanups: number;
  /** 要用幾次（背景＋動作＋清理）。 */
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
  uploads: ReadonlySet<string>,
  plan?: Pick<ProducePlan, "remaining" | "estimate_s" | "fits">,
): PlanCounts {
  const bgEntries = entries.filter((e) => needsBackground(e, uploads));
  const backgrounds = bgEntries.length;
  const cleanups = bgEntries.filter((e) => e.autoCleanup).length;
  const poses = entries.filter(needsPose).length;
  const total = backgrounds + poses + cleanups;
  const remaining = plan ? plan.remaining : undefined;
  const fits = remaining == null ? total : Math.min(total, remaining);
  const perImage = plan && plan.fits > 0 ? plan.estimate_s / plan.fits : SECONDS_PER_IMAGE;
  const minutes = fits > 0 ? Math.max(1, Math.ceil((fits * perImage) / 60)) : 0;
  return { backgrounds, poses, cleanups, total, fits, remaining, minutes };
}

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
  const parts = `背景 ${c.backgrounds}＋動作 ${c.poses}${c.cleanups > 0 ? `＋清掉框外雜物 ${c.cleanups}` : ""}`;
  const heading = `${prefix}會畫 ${c.total} 張圖（${parts}）`;
  if (c.fits === 0) return { heading, detail: NO_QUOTA, defer: null };
  const cap = c.remaining == null ? "" : `（今天會用 ${c.fits} 次，還剩 ${c.remaining} 次）`;
  return {
    heading,
    detail: `大約 ${c.minutes} 分鐘。可以先去忙，好了會通知你${cap}。`,
    defer: c.fits < c.total ? `今天先做 ${c.fits} 張，其餘明天繼續` : null,
  };
}
