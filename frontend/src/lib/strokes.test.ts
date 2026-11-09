import { describe, expect, it } from "vitest";
import {
  ERASE_WIDTH,
  MAX_STROKE_POINTS,
  MAX_STROKES,
  MAX_TOTAL_POINTS,
  clampWidth,
  drawStrokes,
  simplify,
  toPixel,
  toRelative,
  validate,
  type Stroke,
  type StrokeCtx,
} from "./strokes";

const W = 2551;
const H = 3579;

describe("toRelative（對齊後端 apply_strokes：x = px/(w-1)）", () => {
  it("角落點 (0,0) → [0,0]、(w-1,h-1) → [1,1]", () => {
    expect(toRelative(0, 0, W, H)).toEqual([0, 0]);
    expect(toRelative(W - 1, H - 1, W, H)).toEqual([1, 1]);
  });

  it("中間點用 w-1 / h-1 當分母", () => {
    const [x, y] = toRelative(1275, 1789, W, H);
    expect(x).toBeCloseTo(1275 / (W - 1), 12);
    expect(y).toBeCloseTo(1789 / (H - 1), 12);
  });

  it("超出範圍夾在 0–1，非有限值當 0", () => {
    expect(toRelative(-50, H + 20, W, H)).toEqual([0, 1]);
    expect(toRelative(Number.NaN, Number.POSITIVE_INFINITY, W, H)).toEqual([0, 1]);
    expect(toRelative(Number.NaN, Number.NaN, W, H)).toEqual([0, 0]);
  });

  it("寬或高只有 1 px 時不除以 0", () => {
    expect(toRelative(0, 0, 1, 1)).toEqual([0, 0]);
  });

  it("toPixel 是反函數（後端 round(x*(w-1)) 會回到同一個像素）", () => {
    const [x, y] = toRelative(812, 2048, W, H);
    const [px, py] = toPixel(x, y, W, H);
    expect(Math.round(px)).toBe(812);
    expect(Math.round(py)).toBe(2048);
  });
});

describe("simplify", () => {
  it("去掉離上一個保留點太近的點，保留起點與終點", () => {
    const pts: [number, number][] = [
      [0.1, 0.1],
      [0.1001, 0.1],
      [0.1002, 0.1],
      [0.2, 0.1],
      [0.2001, 0.1001],
    ];
    const out = simplify(pts);
    expect(out[0]).toEqual([0.1, 0.1]);
    expect(out[out.length - 1]).toEqual([0.2001, 0.1001]);
    expect(out).not.toContainEqual([0.1001, 0.1]);
    expect(out).not.toContainEqual([0.1002, 0.1]);
  });

  it("單點（只點一下）保留 1 點；全部重疊也只剩 1 點", () => {
    expect(simplify([[0.5, 0.5]])).toEqual([[0.5, 0.5]]);
    expect(simplify([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]])).toEqual([[0.5, 0.5]]);
    expect(simplify([])).toEqual([]);
  });

  it(`單筆上限 ${MAX_STROKE_POINTS} 點（均勻抽樣，起點終點保留）`, () => {
    const n = 7000;
    const pts: [number, number][] = Array.from({ length: n }, (_, i) => [i / (n - 1), (i % 2) * 0.5]);
    const out = simplify(pts);
    expect(out.length).toBeLessThanOrEqual(MAX_STROKE_POINTS);
    expect(out.length).toBeGreaterThan(1000);
    expect(out[0]).toEqual(pts[0]);
    expect(out[out.length - 1]).toEqual(pts[n - 1]);
  });

  it("可自訂 minDistRel", () => {
    const pts: [number, number][] = [
      [0, 0],
      [0.05, 0],
      [0.1, 0],
      [0.15, 0],
    ];
    expect(simplify(pts, 0.08)).toEqual([
      [0, 0],
      [0.1, 0],
      [0.15, 0],
    ]);
  });
});

describe("clampWidth", () => {
  it("夾在後端 2–64，非有限值用擦除預設", () => {
    expect(clampWidth(1)).toBe(2);
    expect(clampWidth(11)).toBe(11);
    expect(clampWidth(100)).toBe(64);
    expect(clampWidth(Number.NaN)).toBe(ERASE_WIDTH);
  });
});

describe("validate（對齊後端 EditsIn 限制）", () => {
  const s = (n: number, extra: Partial<Stroke> = {}): Stroke => ({
    op: "draw",
    width_px: 11,
    points: Array.from({ length: n }, (_, i) => [i / Math.max(1, n), 0.5] as [number, number]),
    ...extra,
  });

  it("合法 → null；空陣列也合法（清掉修改圖層）", () => {
    expect(validate([s(3), s(1, { op: "erase", width_px: 24 })])).toBeNull();
    expect(validate([])).toBeNull();
  });

  it(`超過 ${MAX_STROKES} 筆 → 錯誤訊息`, () => {
    expect(validate(Array.from({ length: MAX_STROKES + 1 }, () => s(1)))).toMatch(/筆/);
    expect(validate(Array.from({ length: MAX_STROKES }, () => s(1)))).toBeNull();
  });

  it(`總點數超過 ${MAX_TOTAL_POINTS} → 錯誤訊息`, () => {
    const strokes = Array.from({ length: 11 }, () => s(MAX_STROKE_POINTS));
    expect(validate(strokes)).toMatch(/點/);
    expect(validate(strokes.slice(0, 10))).toBeNull();
  });

  it("單筆 0 點或超過上限、座標超出 0–1 或非有限、線寬超出 2–64、op 不對 → 錯誤", () => {
    expect(validate([s(0)])).not.toBeNull();
    expect(validate([s(MAX_STROKE_POINTS + 1)])).not.toBeNull();
    expect(validate([{ op: "draw", width_px: 11, points: [[1.2, 0]] }])).not.toBeNull();
    expect(validate([{ op: "draw", width_px: 11, points: [[Number.NaN, 0]] }])).not.toBeNull();
    expect(validate([s(1, { width_px: 1 })])).not.toBeNull();
    expect(validate([s(1, { width_px: 65 })])).not.toBeNull();
    expect(validate([s(1, { op: "paint" as Stroke["op"] })])).not.toBeNull();
  });
});

describe("drawStrokes（疊層預覽，與後端 apply_strokes 同一套座標）", () => {
  function recorder() {
    const calls: [string, ...unknown[]][] = [];
    const ctx = {
      strokeStyle: "" as unknown,
      fillStyle: "" as unknown,
      lineWidth: 0,
      lineCap: "butt",
      lineJoin: "miter",
      save: () => calls.push(["save"]),
      restore: () => calls.push(["restore"]),
      beginPath: () => calls.push(["beginPath"]),
      moveTo: (x: number, y: number) => calls.push(["moveTo", x, y]),
      lineTo: (x: number, y: number) => calls.push(["lineTo", x, y]),
      arc: (x: number, y: number, r: number) => calls.push(["arc", x, y, r]),
      stroke: () => calls.push(["stroke", ctx.strokeStyle, ctx.lineWidth, ctx.lineCap]),
      fill: () => calls.push(["fill", ctx.fillStyle]),
    };
    return { ctx: ctx as unknown as StrokeCtx, calls };
  }

  it("點畫在像素中心：x*(w-1)+0.5；補線黑、擦除白；線寬＝width_px、圓頭", () => {
    const { ctx, calls } = recorder();
    drawStrokes(
      ctx,
      [
        { op: "draw", width_px: 11, points: [[0, 0], [1, 1]] },
        { op: "erase", width_px: 24, points: [[0.5, 0.5], [1, 0]] },
      ],
      101,
      201,
    );
    expect(calls).toContainEqual(["moveTo", 0.5, 0.5]);
    expect(calls).toContainEqual(["lineTo", 100.5, 200.5]);
    expect(calls).toContainEqual(["stroke", "#000000", 11, "round"]);
    expect(calls).toContainEqual(["moveTo", 50.5, 100.5]);
    expect(calls).toContainEqual(["stroke", "#ffffff", 24, "round"]);
  });

  it("單點筆畫畫成實心圓（直徑＝線寬）", () => {
    const { ctx, calls } = recorder();
    drawStrokes(ctx, [{ op: "draw", width_px: 12, points: [[0.5, 0.5]] }], 101, 201);
    expect(calls).toContainEqual(["arc", 50.5, 100.5, 6]);
    expect(calls).toContainEqual(["fill", "#000000"]);
  });
});
