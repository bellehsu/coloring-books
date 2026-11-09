import { describe, expect, it } from "vitest";
import {
  BRUSH_WIDTH,
  INK,
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
  it("全部像素都 ≥ 門檻才算空白", () => {
    expect(isBlank(pixels([[255, 255, 255, 255], [250, 251, 252, 255]]))).toBe(true);
    expect(isBlank(pixels([[255, 255, 255, 255], [249, 255, 255, 255]]))).toBe(false);
    expect(isBlank(pixels([[0, 0, 0, 255]]))).toBe(false);
  });

  it("門檻可調；alpha 不參與判斷", () => {
    expect(isBlank(pixels([[200, 200, 200, 255]]), 200)).toBe(true);
    expect(isBlank(pixels([[255, 255, 255, 0]]))).toBe(true);
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
