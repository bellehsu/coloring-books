import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseStory, planImport, type ExistingPage } from "@/lib/storyImport";
import { applyImport } from "./applyImport";

const pg = (id: number, position: number, scene_prompt: string, action_prompt: string, extra: Partial<ExistingPage> = {}) => ({
  id,
  position,
  scene_prompt,
  action_prompt,
  ...extra,
});
const rowsFor = (text: string, existing: ExistingPage[]) => planImport(parseStory(text).pages, existing);
const TEXT_OK = "第1頁\n場景：森林\n動作：跑步\n第2頁\n場景：海邊\n動作：游泳\n第3頁\n場景：山\n動作：爬山";

let nextId: number;
const createPage = vi.fn();
const updatePage = vi.fn();
beforeEach(() => {
  nextId = 30;
  createPage.mockReset().mockImplementation(async () => ({ id: ++nextId }));
  updatePage.mockReset().mockResolvedValue({});
});

describe("applyImport（貼上整本故事的匯入規則）", () => {
  it("先建立不足的頁，再只填現有頁的空欄位；回傳依頁序的 id", async () => {
    // 第 1 頁全空、第 2 頁只有場景
    const existing = [pg(11, 0, "", ""), pg(12, 1, "舊場景", "")];
    const ids = await applyImport(rowsFor(TEXT_OK, existing), existing, { createPage, updatePage });
    expect(ids).toEqual([11, 12, 31]);
    expect(createPage).toHaveBeenCalledTimes(1);
    expect(createPage).toHaveBeenCalledWith({ scene_prompt: "山", action_prompt: "爬山" });
    expect(updatePage).toHaveBeenCalledTimes(2);
    expect(updatePage).toHaveBeenNthCalledWith(1, { pageId: 11, scene_prompt: "森林", action_prompt: "跑步" });
    expect(updatePage).toHaveBeenNthCalledWith(2, { pageId: 12, action_prompt: "游泳" });
    expect(createPage.mock.invocationCallOrder[0]).toBeLessThan(updatePage.mock.invocationCallOrder[0]);
  });

  it("有問題的段落不套用；被跳過的中間段落補空白頁維持頁序", async () => {
    const text = `第1頁\n場景：${"x".repeat(801)}\n動作：跑\n第2頁\n場景：海\n動作：游`;
    const ids = await applyImport(rowsFor(text, []), [], { createPage, updatePage });
    expect(ids).toEqual([31, 32]);
    expect(createPage).toHaveBeenNthCalledWith(1, { scene_prompt: "", action_prompt: "" });
    expect(createPage).toHaveBeenNthCalledWith(2, { scene_prompt: "海", action_prompt: "游" });
    expect(updatePage).not.toHaveBeenCalled();
  });

  it("新增的頁先建立，再用 update 補圖下方的文字；已有文字的欄位不覆蓋", async () => {
    const existing = [pg(11, 0, "", "", { caption_zh: "舊", caption_en: null })];
    const text = "第1頁\n場景：森林\n動作：跑步\n中文：小熊\n英文：Bear\n第2頁\n場景：山\n動作：爬山\n中文：登山";
    await applyImport(rowsFor(text, existing), existing, { createPage, updatePage });
    expect(updatePage).toHaveBeenCalledWith({ pageId: 31, caption_zh: "登山" });
    expect(updatePage).toHaveBeenCalledWith({ pageId: 11, scene_prompt: "森林", action_prompt: "跑步", caption_en: "Bear" });
  });

  it("全部都有內容：什麼都不送", async () => {
    const existing = [pg(11, 0, "有", "有")];
    expect(await applyImport(rowsFor("場景：森林\n動作：跑步", existing), existing, { createPage, updatePage })).toEqual([11]);
    expect(createPage).not.toHaveBeenCalled();
    expect(updatePage).not.toHaveBeenCalled();
  });

  it("主題包的書：建立的頁和填了內容的現有頁都設成簡單線條；沒填內容的頁不動", async () => {
    const existing = [pg(11, 0, "", ""), pg(12, 1, "舊場景", "舊動作")];
    await applyImport(rowsFor(TEXT_OK, existing), existing, { createPage, updatePage }, { lineDetail: "simple" });
    expect(updatePage).toHaveBeenCalledWith({ pageId: 11, scene_prompt: "森林", action_prompt: "跑步", line_detail: "simple" });
    expect(updatePage).toHaveBeenCalledWith({ pageId: 31, line_detail: "simple" });
    expect(updatePage.mock.calls.some(([b]) => b.pageId === 12)).toBe(false);
  });

  it("主題包帶位置：建立的頁和填了內容的現有頁存位置；沒填內容的頁不動", async () => {
    const savePlacement = vi.fn().mockResolvedValue({});
    const P = (cx: number) => ({ cx, cy: 0.75, height: 0.42, flip: false });
    const existing = [pg(11, 0, "", ""), pg(12, 1, "舊場景", "舊動作")];
    await applyImport(rowsFor(TEXT_OK, existing), existing, { createPage, updatePage, savePlacement }, { placements: [P(0.26), P(0.74), P(0.26)] });
    expect(savePlacement).toHaveBeenCalledWith(11, P(0.26));
    expect(savePlacement).toHaveBeenCalledWith(31, P(0.26));
    expect(savePlacement.mock.calls.some(([id]) => id === 12)).toBe(false);
  });

  it("主題包帶清理設定：建立的頁和填了內容的現有頁存 auto_cleanup／cleanup_prompt（和場景同一個 PATCH）；沒填內容的頁不動", async () => {
    const existing = [pg(11, 0, "", ""), pg(12, 1, "舊場景", "舊動作")];
    await applyImport(rowsFor(TEXT_OK, existing), existing, { createPage, updatePage }, {
      cleanups: [{ auto: true }, { auto: true }, { auto: true, prompt: "Only the clover." }],
    });
    expect(updatePage).toHaveBeenCalledWith({
      pageId: 11, scene_prompt: "森林", action_prompt: "跑步", auto_cleanup: true, cleanup_prompt: null,
    });
    expect(updatePage).toHaveBeenCalledWith({ pageId: 31, auto_cleanup: true, cleanup_prompt: "Only the clover." });
    expect(updatePage.mock.calls.some(([b]) => b.pageId === 12)).toBe(false);
  });

  it("清理設定比頁數短：沒有設定的頁不送清理欄位", async () => {
    await applyImport(rowsFor(TEXT_OK, []), [], { createPage, updatePage }, { cleanups: [{ auto: true }] });
    expect(updatePage).toHaveBeenCalledTimes(1);
    expect(updatePage).toHaveBeenCalledWith({ pageId: 31, auto_cleanup: true, cleanup_prompt: null });
  });

  it("不放主角：只有建立的頁送 include_character；既有頁不送（走 from_setup PATCH）", async () => {
    const existing = [pg(11, 0, "", "")];
    await applyImport(rowsFor(TEXT_OK, existing), existing, { createPage, updatePage }, { includeCharacter: (n) => (n === 1 || n === 3 ? false : undefined) });
    expect(updatePage).toHaveBeenCalledWith({ pageId: 32, include_character: false }); // 第 3 頁（新）
    for (const [arg] of updatePage.mock.calls) {
      if (arg.pageId === 11) expect(arg).not.toHaveProperty("include_character");
    }
    expect(updatePage.mock.calls.filter(([a]) => a.pageId === 31 && "include_character" in a)).toHaveLength(0);
  });

  it("沒給 includeCharacter（或回 undefined）：不多送請求", async () => {
    await applyImport(rowsFor(TEXT_OK, []), [], { createPage, updatePage }, { includeCharacter: () => undefined });
    expect(updatePage).not.toHaveBeenCalled();
  });
});
