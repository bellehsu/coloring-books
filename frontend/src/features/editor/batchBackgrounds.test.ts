import { describe, expect, it } from "vitest";
import type { Generation, Page } from "@/api/client";
import { batchConfirmText, batchResultText, planBatchBackgrounds, sketchPageCount } from "./batchBackgrounds";

const page = (id: number, o: Partial<Page> = {}): Page =>
  ({ id, position: id - 1, scene_prompt: `scene ${id}`, background_generation_id: null, ...o }) as Page;
const gen = (id: number, o: Partial<Generation> = {}): Generation =>
  ({ id, page_id: 1, kind: "background", status: "succeeded", discarded: false, ...o }) as Generation;

describe("planBatchBackgrounds", () => {
  it("三個略過條件（依頁面順序），其他頁排入", () => {
    const pages = [page(1), page(2, { scene_prompt: "   " }), page(3, { background_generation_id: 9 }), page(4), page(5)];
    const gens = [
      gen(10, { page_id: 4, status: "queued" }),
      gen(11, { page_id: 5, status: "running" }),
    ];
    expect(planBatchBackgrounds(pages, gens)).toEqual({
      queued: [1],
      skipped: [
        { pageId: 2, n: 2, reason: "沒有場景描述" },
        { pageId: 3, n: 3, reason: "已有背景" },
        { pageId: 4, n: 4, reason: "背景產生中" },
        { pageId: 5, n: 5, reason: "背景產生中" },
      ],
      estimate: false,
    });
  });

  it("歷史裡有成功、未捨棄、沒選用的背景 → 已有背景；只有捨棄或失敗的不算", () => {
    const pages = [page(1), page(2), page(3)];
    const gens = [
      gen(10, { page_id: 1 }),
      gen(11, { page_id: 2, discarded: true }),
      gen(12, { page_id: 3, status: "failed" }),
      gen(13, { page_id: 3, kind: "pose" }),
    ];
    const plan = planBatchBackgrounds(pages, gens);
    expect(plan.queued).toEqual([2, 3]);
    expect(plan.skipped).toEqual([{ pageId: 1, n: 1, reason: "已有背景" }]);
  });

  it("舊流程：選用的是整頁候選圖（kind page）→ 已有背景；選用合成圖但沒背景欄位的不算", () => {
    const pages = [page(1, { selected_generation_id: 20 }), page(2, { selected_generation_id: 21 }), page(3)];
    const gens = [gen(20, { page_id: 1, kind: "page" }), gen(21, { page_id: 2, kind: "composite" })];
    const plan = planBatchBackgrounds(pages, gens);
    expect(plan.queued).toEqual([2, 3]);
    expect(plan.skipped).toEqual([{ pageId: 1, n: 1, reason: "已有背景" }]);
  });

  it("生成紀錄還沒載入：只用頁面欄位估計", () => {
    const plan = planBatchBackgrounds([page(1), page(2, { scene_prompt: "" }), page(3, { background_generation_id: 4 })], undefined);
    expect(plan).toEqual({
      queued: [1],
      skipped: [
        { pageId: 2, n: 2, reason: "沒有場景描述" },
        { pageId: 3, n: 3, reason: "已有背景" },
      ],
      estimate: true,
    });
  });
});

describe("batchConfirmText", () => {
  const three = { queued: [1, 2, 3], skipped: [], estimate: false };
  const skips = [
    { pageId: 4, n: 4, reason: "沒有場景描述" },
    { pageId: 5, n: 5, reason: "已有背景" },
  ];

  it("簡潔、沒有草圖頁", () => {
    expect(batchConfirmText(three, "simple", 0)).toBe("會為 3 頁產生背景（簡潔）");
  });
  it("細緻", () => {
    expect(batchConfirmText(three, "detailed", 0)).toBe("會為 3 頁產生背景（細緻）");
  });
  it("簡潔＋有草圖的頁：括號裡加上「，有草圖的 K 頁用細緻」", () => {
    expect(batchConfirmText(three, "simple", 1)).toBe("會為 3 頁產生背景（簡潔，有草圖的 1 頁用細緻）");
  });
  it("細緻不加草圖說明（本來就全部細緻）", () => {
    expect(batchConfirmText(three, "detailed", 2)).toBe("會為 3 頁產生背景（細緻）");
  });
  it("列出略過的頁與原因（風格之後）", () => {
    expect(batchConfirmText({ queued: [1, 2], skipped: skips, estimate: false }, "simple", 0)).toBe(
      "會為 2 頁產生背景（簡潔；2 頁略過：第 4 頁沒有場景描述、第 5 頁已有背景）",
    );
    expect(batchConfirmText({ queued: [1, 2], skipped: skips, estimate: false }, "simple", 1)).toBe(
      "會為 2 頁產生背景（簡潔，有草圖的 1 頁用細緻；2 頁略過：第 4 頁沒有場景描述、第 5 頁已有背景）",
    );
  });
});

describe("sketchPageCount", () => {
  it("只算會排入、有草圖且構圖強度 > 0 的頁", () => {
    const pages = [
      page(1, { sketch_url: "/s/1", controlnet_strength: 0.4 }),
      page(2, { sketch_url: "/s/2", controlnet_strength: 0 }),
      page(3, { sketch_url: null, controlnet_strength: 0.4 }),
      page(4, { sketch_url: "/s/4", controlnet_strength: 0.3 }), // 略過的頁不算
      page(5, { sketch_url: "/s/5", controlnet_strength: 0.05 }),
    ];
    const plan = { queued: [1, 2, 3, 5], skipped: [{ pageId: 4, n: 4, reason: "已有背景" }], estimate: false };
    expect(sketchPageCount(plan, pages)).toBe(2);
  });
});

describe("batchResultText", () => {
  const pages = [page(1), page(2), page(3)];
  it("只有排入", () => {
    expect(batchResultText({ queued: [{} as Generation, {} as Generation], skipped: [] }, pages)).toBe("已排入 2 頁背景");
  });
  it("有略過：加上「、略過 M 頁」", () => {
    expect(
      batchResultText(
        { queued: [{} as Generation], skipped: [{ kind: "background", page_id: 3, position: 2, reason: "沒有場景描述" }] },
        pages,
      ),
    ).toBe("已排入 1 頁背景、略過 1 頁");
  });
  it("前端無法預先知道的原因（草圖檔案遺失）列出頁碼", () => {
    expect(
      batchResultText(
        {
          queued: [],
          skipped: [
            { kind: "background", page_id: 2, position: 1, reason: "草圖檔案遺失" },
            { kind: "background", page_id: 3, position: 2, reason: "已有背景" },
          ],
        },
        pages,
      ),
    ).toBe("已排入 0 頁背景、略過 2 頁（第 2 頁草圖檔案遺失）");
  });
});
