import { describe, expect, it } from "vitest";
import type { Generation } from "@/api/client";
import { buildPreview, pageMaterials, planCounts, planText } from "./storyPreview";

const page = (id: number, position: number, scene = "", action = "", extra: Record<string, unknown> = {}) => ({
  id,
  position,
  scene_prompt: scene,
  action_prompt: action,
  caption_zh: null,
  caption_en: null,
  background_generation_id: null,
  pose_generation_id: null,
  selected_generation_id: null,
  redraw_background: false,
  redraw_pose: false,
  ...extra,
});
const gen = (id: number, o: Partial<Generation>): Generation =>
  ({ id, page_id: null, kind: "background", status: "succeeded", discarded: false, source: "generated", ...o }) as Generation;

const STORY = "第1頁\n場景：消防局門口\n動作：開著消防車揮手\n中文：消防車\n英文：Fire Engine\n\n第2頁\n場景：醫院門口\n動作：站在救護車旁邊\n\n第3頁\n場景：公園\n動作：開心地跳起來";

describe("buildPreview", () => {
  it("新書：每段一頁，帶頁碼、場景、動作與圖下方的文字", () => {
    const p = buildPreview([], STORY);
    expect(p.pasted).toBe(3);
    expect(p.entries.map((e) => [e.n, e.pageId, e.scene, e.action, e.captionZh, e.captionEn])).toEqual([
      [1, null, "消防局門口", "開著消防車揮手", "消防車", "Fire Engine"],
      [2, null, "醫院門口", "站在救護車旁邊", "", ""],
      [3, null, "公園", "開心地跳起來", "", ""],
    ]);
    expect(p.problems).toEqual([]);
  });

  it("已有的書：現有頁照舊（只填空欄位），多的段落接在後面", () => {
    const p = buildPreview([page(12, 1, "舊場景", ""), page(11, 0, "", "")], STORY);
    expect(p.entries.map((e) => [e.n, e.pageId, e.scene, e.action])).toEqual([
      [1, 11, "消防局門口", "開著消防車揮手"],
      [2, 12, "舊場景", "站在救護車旁邊"],
      [3, null, "公園", "開心地跳起來"],
    ]);
  });

  it("沒貼故事：只列現有頁；看不懂與超過字數的段落列成問題", () => {
    expect(buildPreview([page(11, 0, "森林", "跑")], "").entries).toHaveLength(1);
    const p = buildPreview([], `場景：${"x".repeat(801)}\n動作：跑\n\n場景：海\n動作：游\n\n希望對你有幫助！`);
    expect(p.problems).toEqual([
      { n: 1, text: "場景超過 800 字" },
      { n: null, text: "找不到「場景：」或「動作：」：希望對你有幫助！" },
    ]);
    // 第 1 段不套用：補成空白頁，第 2 段還是第 2 頁
    expect(p.entries.map((e) => [e.n, e.scene])).toEqual([
      [1, ""],
      [2, "海"],
    ]);
  });
});

describe("buildPreview：手動加的頁", () => {
  it("接在現有與貼上的頁後面，key 跟著 draft 不跟頁碼", () => {
    const drafts = [{ key: 7, scene: "森林", action: "" }];
    const alone = buildPreview([], "", new Map(), drafts).entries;
    expect(alone.map((e) => [e.n, e.key, e.draft, e.scene])).toEqual([[1, "d7", 7, "森林"]]);
    const after = buildPreview([page(11, 0, "舊", "")], STORY, new Map(), drafts).entries;
    expect(after.map((e) => [e.n, e.key])).toEqual([[1, "p1"], [2, "p2"], [3, "p3"], [4, "d7"]]);
    expect(planCounts(after, new Set(["d7"]))).toMatchObject({ backgrounds: 3, poses: 3 }); // 第 4 頁（d7）上傳
  });
});

describe("pageMaterials", () => {
  it("已選、成功、排隊中都算有；捨棄、失敗不算；上傳的背景另外標出", () => {
    const pages = [
      page(1, 0, "a", "b", { background_generation_id: 50 }),
      page(2, 1, "a", "b"),
      page(3, 2, "a", "b"),
      page(4, 3, "a", "b", { selected_generation_id: 70 }),
    ];
    const gens = [
      gen(50, { page_id: 1, source: "upload" }),
      gen(51, { page_id: 2, status: "queued" }),
      gen(52, { page_id: 2, kind: "pose", status: "failed" }),
      gen(53, { page_id: 3, kind: "pose" }),
      gen(54, { page_id: 3, discarded: true }),
      gen(70, { page_id: 4, kind: "page" }),
    ];
    const m = pageMaterials(pages, gens);
    expect(m.get(1)).toMatchObject({ hasBackground: true, backgroundUploaded: true, hasPose: false });
    expect(m.get(2)).toMatchObject({ hasBackground: true, backgroundUploaded: false, hasPose: false });
    expect(m.get(3)).toMatchObject({ hasBackground: false, backgroundUploaded: false, hasPose: true });
    // 舊流程整頁圖：兩種都不再畫
    expect(m.get(4)).toMatchObject({ hasBackground: true, backgroundUploaded: false, hasPose: true });
  });
});

describe("planCounts／planText", () => {
  const entries = buildPreview([], STORY).entries;

  it("背景＝沒上傳的頁、動作＝有動作的頁；今天放得下", () => {
    const c = planCounts(entries, new Set(["p3"]), { remaining: 60, estimate_s: 0, fits: 0 });
    expect(c).toMatchObject({ backgrounds: 2, poses: 3, total: 5, fits: 5, remaining: 60, minutes: 2 });
    expect(planText(c, true)).toEqual({
      heading: "準備好了！會畫 5 張圖（背景 2＋動作 3）",
      detail: "大約 2 分鐘。可以先去忙，好了會通知你（今天會用 5 次，還剩 60 次）。",
      defer: null,
    });
  });

  it("已經有素材的頁不算", () => {
    const withMaterials = buildPreview(
      [page(11, 0, "消防局門口", "開著消防車揮手")],
      STORY,
      new Map([[11, { hasBackground: true, backgroundUploaded: false, hasPose: true, redrawBackground: false, redrawPose: false }]]),
    ).entries;
    expect(planCounts(withMaterials, new Set())).toMatchObject({ backgrounds: 2, poses: 2, total: 4, remaining: undefined });
  });

  it("放不下：今天先做 X 張，其餘明天繼續；秒數用 /produce/plan 的換算", () => {
    const c = planCounts(entries, new Set(), { remaining: 4, estimate_s: 120, fits: 4 });
    expect(c).toMatchObject({ total: 6, fits: 4, minutes: 2 });
    const t = planText(c, true);
    expect(t.detail).toBe("大約 2 分鐘。可以先去忙，好了會通知你（今天會用 4 次，還剩 4 次）。");
    expect(t.defer).toBe("今天先做 4 張，其餘明天繼續");
  });

  it("一次都不剩、不限次數、沒有要畫的", () => {
    expect(planText(planCounts(entries, new Set(), { remaining: 0, estimate_s: 0, fits: 0 }), true)).toEqual({
      heading: "準備好了！會畫 6 張圖（背景 3＋動作 3）",
      detail: "今天的生成次數已用完，明天再來",
      defer: null,
    });
    const admin = planCounts(entries, new Set(), { remaining: null, estimate_s: 132, fits: 6 });
    expect(planText(admin, false)).toEqual({
      heading: "會畫 6 張圖（背景 3＋動作 3）",
      detail: "大約 3 分鐘。可以先去忙，好了會通知你。",
      defer: null,
    });
    expect(planText(planCounts([], new Set()), true).heading).toBe("準備好了！不用再畫新的圖");
  });
});

describe("redraw flags", () => {
  it("flagged pages count in planCounts", () => {
    const pages = [page(1, 0, "a", "b", { redraw_pose: true }), page(2, 1, "c", "d", { redraw_background: true })];
    const gens = [
      gen(1, { page_id: 1 }),
      gen(2, { page_id: 1, kind: "pose" }),
      gen(3, { page_id: 2 }),
      gen(4, { page_id: 2, kind: "pose" }),
    ];
    const m = pageMaterials(pages, gens);
    expect(m.get(1)).toMatchObject({ hasBackground: true, hasPose: true, redrawBackground: false, redrawPose: true });
    const entries = buildPreview(pages, "", m).entries;
    expect(planCounts(entries, new Set())).toMatchObject({ backgrounds: 1, poses: 1, total: 2 });
  });
});

describe("自動清理（背景＋清掉框外雜物，算 2 次）", () => {
  it("主題包的清理設定：要畫背景的頁多算一次清理；標題列出清掉框外雜物", () => {
    const entries = buildPreview([], STORY, new Map(), [], [{ auto: true }, { auto: false }, { auto: true }]).entries;
    expect(entries.map((e) => e.autoCleanup)).toEqual([true, false, true]);
    const c = planCounts(entries, new Set(["p3"]), { remaining: 60, estimate_s: 0, fits: 0 });
    expect(c).toMatchObject({ backgrounds: 2, poses: 3, cleanups: 1, total: 6, fits: 6 });
    expect(planText(c, false).heading).toBe("會畫 6 張圖（背景 2＋動作 3＋清掉框外雜物 1）");
  });

  it("已經開著自動清理的現有頁也算；貼上的場景填進空的場景會關掉它（後端規則），主題包明確帶的優先", () => {
    const pages = [page(11, 0, "", "x", { auto_cleanup: true }), page(12, 1, "海邊", "", { auto_cleanup: true })];
    const m = pageMaterials(pages, []);
    expect(m.get(11)).toMatchObject({ autoCleanup: true });
    const pasted = buildPreview(pages, STORY, m).entries;
    expect(pasted.map((e) => e.autoCleanup)).toEqual([false, true, false]);
    expect(buildPreview(pages, STORY, m, [], [{ auto: true }]).entries[0].autoCleanup).toBe(true);
    const none = buildPreview(pages, "", m).entries;
    expect(planCounts(none, new Set())).toMatchObject({ backgrounds: 1, cleanups: 1, total: 3 });
  });
});

