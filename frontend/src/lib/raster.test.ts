import { describe, expect, it } from "vitest";
import {
  BRUSH_WIDTH,
  INK,
  INK_THRESHOLD,
  MAX_INK_PIXELS,
  MAX_UNDO,
  PAPER,
  drawShape,
  drawStroke,
  guidesFromLayout,
  isBlank,
  pushHistory,
  toCanvasPoint,
  type RasterCtx,
} from "./raster";

type Call = [string, ...unknown[]];

/** 記錄所有呼叫與屬性設定的假 context。 */
function fakeCtx() {
  const calls: Call[] = [];
  const rec =
    (name: string) =>
    (...args: unknown[]) => {
      calls.push([name, ...args]);
    };
  const ctx = {
    strokeStyle: "" as unknown,
    fillStyle: "" as unknown,
    lineWidth: 0,
    lineCap: "butt",
    lineJoin: "miter",
    save: rec("save"),
    restore: rec("restore"),
    beginPath: rec("beginPath"),
    closePath: rec("closePath"),
    moveTo: rec("moveTo"),
    lineTo: rec("lineTo"),
    arc: rec("arc"),
    ellipse: rec("ellipse"),
    stroke: () => calls.push(["stroke", ctx.strokeStyle, ctx.lineWidth]),
    fill: () => calls.push(["fill", ctx.fillStyle]),
    strokeRect: (...a: unknown[]) => calls.push(["strokeRect", ...a, ctx.strokeStyle, ctx.lineWidth]),
    fillRect: rec("fillRect"),
  };
  return { ctx: ctx as unknown as RasterCtx, calls };
}

function pixels(values: number[][]): ImageData {
  return { data: new Uint8ClampedArray(values.flat()), width: values.length, height: 1 } as unknown as ImageData;
}

describe("isBlank", () => {
  const W = [255, 255, 255, 255];
  const K = [0, 0, 0, 255];
  const rep = (px: number[], n: number) => Array.from({ length: n }, () => px);

  it("全白是空白", () => {
    expect(isBlank(pixels(rep(W, 100)))).toBe(true);
  });

  it("橡皮擦留下的淺灰毛邊（例如 200）不算墨水 → 空白", () => {
    expect(isBlank(pixels([...rep(W, 50), ...rep([200, 200, 200, 255], 30)]))).toBe(true);
  });

  it(`少於 ${MAX_INK_PIXELS} 個深色像素仍是空白；20 個就不是`, () => {
    expect(MAX_INK_PIXELS).toBe(8);
    expect(isBlank(pixels([...rep(W, 50), ...rep(K, 7)]))).toBe(true);
    expect(isBlank(pixels([...rep(W, 50), ...rep(K, MAX_INK_PIXELS)]))).toBe(false);
    expect(isBlank(pixels([...rep(W, 50), ...rep(K, 20)]))).toBe(false);
  });

  it(`任一通道 < ${INK_THRESHOLD} 就算深色（彩色墨水也算）`, () => {
    expect(INK_THRESHOLD).toBe(128);
    expect(isBlank(pixels(rep([255, 255, INK_THRESHOLD - 1, 255], 20)))).toBe(false);
    expect(isBlank(pixels(rep([INK_THRESHOLD, INK_THRESHOLD, INK_THRESHOLD, 255], 20)))).toBe(true);
  });

  it("alpha 不參與判斷", () => {
    expect(isBlank(pixels(rep([255, 255, 255, 0], 20)))).toBe(true);
  });
});

describe("drawShape", () => {
  it("rect：拖曳方向不影響，strokeRect 收到左上角與正的寬高，黑色、指定線寬", () => {
    const { ctx, calls } = fakeCtx();
    drawShape(ctx, "rect", { x: 50, y: 80 }, { x: 10, y: 20 }, 6);
    expect(calls.find((c) => c[0] === "strokeRect")).toEqual(["strokeRect", 10, 20, 40, 60, INK, 6]);
  });

  it("line：從 a 到 b", () => {
    const { ctx, calls } = fakeCtx();
    drawShape(ctx, "line", { x: 1, y: 2 }, { x: 30, y: 40 }, 6);
    expect(calls).toContainEqual(["moveTo", 1, 2]);
    expect(calls).toContainEqual(["lineTo", 30, 40]);
    expect(calls).toContainEqual(["stroke", INK, 6]);
  });

  it("circle：內切於拖曳框的橢圓", () => {
    const { ctx, calls } = fakeCtx();
    drawShape(ctx, "circle", { x: 10, y: 10 }, { x: 50, y: 30 }, 6);
    const e = calls.find((c) => c[0] === "ellipse")!;
    expect(e.slice(1, 5)).toEqual([30, 20, 20, 10]);
    expect(calls).toContainEqual(["stroke", INK, 6]);
  });
});

describe("drawStroke", () => {
  it("brush 用黑色圓頭折線", () => {
    const { ctx, calls } = fakeCtx();
    drawStroke(ctx, "brush", [{ x: 0, y: 0 }, { x: 5, y: 5 }, { x: 9, y: 2 }], BRUSH_WIDTH);
    expect(calls).toContainEqual(["moveTo", 0, 0]);
    expect(calls).toContainEqual(["lineTo", 5, 5]);
    expect(calls).toContainEqual(["lineTo", 9, 2]);
    expect(calls).toContainEqual(["stroke", INK, BRUSH_WIDTH]);
    expect((ctx as unknown as { lineCap: string }).lineCap).toBe("round");
  });

  it("eraser 畫白色", () => {
    const { ctx, calls } = fakeCtx();
    drawStroke(ctx, "eraser", [{ x: 0, y: 0 }, { x: 5, y: 5 }], 24);
    expect(calls).toContainEqual(["stroke", PAPER, 24]);
  });

  it("單點畫成實心圓點", () => {
    const { ctx, calls } = fakeCtx();
    drawStroke(ctx, "brush", [{ x: 7, y: 8 }], 6);
    expect(calls.find((c) => c[0] === "arc")?.slice(1, 4)).toEqual([7, 8, 3]);
    expect(calls).toContainEqual(["fill", INK]);
  });

  it("沒有點就什麼都不畫", () => {
    const { ctx, calls } = fakeCtx();
    drawStroke(ctx, "brush", [], 6);
    expect(calls.filter((c) => c[0] === "stroke" || c[0] === "fill")).toEqual([]);
  });
});

describe("toCanvasPoint", () => {
  it("顯示縮放 0.5 時，螢幕座標乘 2 回到畫布像素", () => {
    const rect = { left: 100, top: 50, width: 432, height: 608 };
    expect(toCanvasPoint({ clientX: 100, clientY: 50 }, rect, { width: 864, height: 1216 })).toEqual({ x: 0, y: 0 });
    expect(toCanvasPoint({ clientX: 200, clientY: 150 }, rect, { width: 864, height: 1216 })).toEqual({
      x: 200,
      y: 200,
    });
  });
});

describe("pushHistory", () => {
  it(`最多保留 ${MAX_UNDO} 步，超過時丟掉最舊的`, () => {
    let h: number[] = [];
    for (let i = 1; i <= MAX_UNDO + 1; i++) h = pushHistory(h, i);
    expect(h).toHaveLength(MAX_UNDO);
    expect(h[0]).toBe(2);
    expect(h[h.length - 1]).toBe(MAX_UNDO + 1);
  });

  it("不改動原陣列", () => {
    const h = [1, 2];
    pushHistory(h, 3);
    expect(h).toEqual([1, 2]);
  });
});

describe("guidesFromLayout", () => {
  it("家用模式（沒有 trim/safe）回傳 null", () => {
    expect(guidesFromLayout({ content_px: [2244, 3271], trim_box_px: null, safe_box_px: null })).toBeNull();
  });

  it("印刷廠：box 除以 content_px 得到比例", () => {
    const g = guidesFromLayout({
      content_px: [1000, 2000],
      trim_box_px: [35, 70, 965, 1930],
      safe_box_px: [100, 200, 900, 1800],
    })!;
    expect(g.trim).toEqual({ x: 0.035, y: 0.035, w: 0.93, h: 0.93 });
    expect(g.safe).toEqual({ x: 0.1, y: 0.1, w: 0.8, h: 0.8 });
  });
});
