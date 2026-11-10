import { describe, expect, it } from "vitest";
import type { Generation, Page } from "@/api/client";
import { batchConfirmText, batchResultText, planBatchBackgrounds } from "./batchBackgrounds";

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
  it("沒有略過", () => {
    expect(batchConfirmText({ queued: [1, 2], skipped: [], estimate: false })).toBe("會為 2 頁產生背景");
  });
  it("列出略過的頁與原因", () => {
    expect(
      batchConfirmText({
        queued: [1, 2],
        skipped: [
          { pageId: 3, n: 3, reason: "沒有場景描述" },
          { pageId: 4, n: 4, reason: "已有背景" },
        ],
        estimate: false,
      }),
    ).toBe("會為 2 頁產生背景（2 頁略過：第 3 頁沒有場景描述、第 4 頁已有背景）");
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
        { queued: [{} as Generation], skipped: [{ page_id: 3, position: 2, reason: "沒有場景描述" }] },
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
            { page_id: 2, position: 1, reason: "草圖檔案遺失" },
            { page_id: 3, position: 2, reason: "已有背景" },
          ],
        },
        pages,
      ),
    ).toBe("已排入 0 頁背景、略過 2 頁（第 2 頁草圖檔案遺失）");
  });
});
