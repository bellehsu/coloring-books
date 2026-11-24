import { describe, expect, it } from "vitest";
import { DEFAULT_PLACEMENT } from "@/api/client";
import {
  clampPlacement,
  cornerResize,
  currentSide,
  currentSize,
  movePlacement,
  placementBox,
  resizePlacement,
  withSide,
  withSize,
} from "./placement";

describe("clampPlacement portrait fields", () => {
  it("keeps frame and autofit and allows height up to 2 only with a frame", () => {
    const p = { cx: 0.5, cy: 0.5, height: 3, flip: false, frame: { x: 0.4, y: 0.6 }, autofit: true };
    expect(clampPlacement(p)).toEqual({ ...p, height: 2 });
    expect(clampPlacement({ ...p, height: 1.5 }).height).toBe(1.5);
    expect(clampPlacement({ ...p, frame: null })).toEqual({ ...p, frame: null, height: 1 });
    expect(clampPlacement({ ...p, frame: undefined, autofit: false })).toEqual({ cx: 0.5, cy: 0.5, height: 1, flip: false, autofit: false });
  });
  it("adds no keys when the placement has no portrait fields", () => {
    expect(Object.keys(clampPlacement({ cx: 0.5, cy: 0.5, height: 0.5, flip: false }))).toEqual(["cx", "cy", "height", "flip"]);
  });
});

describe("clampPlacement", () => {
  it("keeps values inside the ranges (cx, cy 0–1; height 0.1–1.0) and keeps flip", () => {
    expect(clampPlacement({ cx: -0.2, cy: 1.4, height: 0.02, flip: true })).toEqual({ cx: 0, cy: 1, height: 0.1, flip: true });
    expect(clampPlacement({ cx: 1.5, cy: -1, height: 3, flip: false })).toEqual({ cx: 1, cy: 0, height: 1, flip: false });
  });

  it("boundaries are allowed exactly", () => {
    expect(clampPlacement({ cx: 0, cy: 1, height: 0.1, flip: false })).toEqual({ cx: 0, cy: 1, height: 0.1, flip: false });
    expect(clampPlacement({ cx: 1, cy: 0, height: 1, flip: false })).toEqual({ cx: 1, cy: 0, height: 1, flip: false });
  });

  it("rounds to 4 decimals (same precision the backend matches on)", () => {
    expect(clampPlacement({ cx: 0.66 + 0.01, cy: 0.123456, height: 0.6, flip: false })).toEqual({
      cx: 0.67,
      cy: 0.1235,
      height: 0.6,
      flip: false,
    });
  });

  it("non-finite numbers fall back to the default", () => {
    expect(clampPlacement({ cx: NaN, cy: Infinity, height: NaN, flip: false })).toEqual(DEFAULT_PLACEMENT);
  });
});

describe("movePlacement / resizePlacement", () => {
  it("moves by relative amounts and clamps", () => {
    expect(movePlacement(DEFAULT_PLACEMENT, 0.01, -0.05)).toEqual({ ...DEFAULT_PLACEMENT, cx: 0.51, cy: 0.61 });
    expect(movePlacement({ ...DEFAULT_PLACEMENT, cx: 0.99 }, 0.05, 0)).toMatchObject({ cx: 1 });
  });

  it("resizes height additively and clamps to 0.1–1.0", () => {
    expect(resizePlacement(DEFAULT_PLACEMENT, 0.05)).toEqual({ ...DEFAULT_PLACEMENT, height: 0.65 });
    expect(resizePlacement({ ...DEFAULT_PLACEMENT, height: 0.12 }, -0.05)).toMatchObject({ height: 0.1 });
    expect(resizePlacement({ ...DEFAULT_PLACEMENT, height: 0.98 }, 0.05)).toMatchObject({ height: 1 });
  });
});

describe("placementBox", () => {
  it("matches the backend: height × stage height, width from aspect, centred on cx, cy", () => {
    // 背景 1000×800，角色高 0.5 → 400 px，寬 = 400 × 0.5 = 200；中心 (500, 400)
    expect(placementBox({ cx: 0.5, cy: 0.5, height: 0.5, flip: false }, 0.5, 1000, 800)).toEqual({
      left: 400,
      top: 200,
      width: 200,
      height: 400,
    });
  });

  it("can extend past the stage (clipped on screen and in the composite)", () => {
    const b = placementBox({ cx: 1, cy: 1, height: 1, flip: true }, 0.5, 864, 1216);
    expect(b).toEqual({ left: 864 - 304, top: 608, width: 608, height: 1216 });
  });
});

describe("cornerResize", () => {
  // 寬／高（相對於舞台寬與舞台高）＝ k；這裡 k = 0.5：高 0.4 → 寬 0.2
  const start = { cx: 0.5, cy: 0.5, height: 0.4, flip: false };

  it("keeps the top-left corner fixed while growing", () => {
    const p = cornerResize(start, 0.5, 0, 0.2); // 往下拉 0.2
    expect(p.height).toBeCloseTo(0.6);
    // 左上角原本 (0.4, 0.3)
    expect(p.cx - (p.height * 0.5) / 2).toBeCloseTo(0.4);
    expect(p.cy - p.height / 2).toBeCloseTo(0.3);
  });

  it("follows whichever axis is pulled further (horizontal pull also scales)", () => {
    const p = cornerResize(start, 0.5, 0.1, 0); // 寬 0.2 → 0.3，高跟著 0.6
    expect(p.height).toBeCloseTo(0.6);
  });

  it("clamps to the minimum height", () => {
    expect(cornerResize(start, 0.5, -1, -1).height).toBe(0.1);
  });

  it("caps at 1 without a frame and at 2 in portrait (frame set), keeping frame/autofit", () => {
    expect(cornerResize(start, 0.5, 0, 5).height).toBe(1);
    const portrait = { ...start, frame: { x: 0.5, y: 0.55 }, autofit: false };
    const big = cornerResize(portrait, 0.5, 0, 1.2); // 0.4 + 1.2 = 1.6
    expect(big.height).toBeCloseTo(1.6);
    expect(big.frame).toEqual({ x: 0.5, y: 0.55 });
    expect(big.autofit).toBe(false);
    expect(cornerResize(portrait, 0.5, 0, 5).height).toBe(2);
  });
});

describe("position presets", () => {
  it("withSide only changes cx (left 0.26) and keeps size and flip", () => {
    expect(withSide({ cx: 0.5, cy: 0.66, height: 0.6, flip: true }, "left")).toEqual({ cx: 0.26, cy: 0.66, height: 0.6, flip: true });
  });
  it("withSize keeps cx and the feet (bottom edge 0.96) where they were", () => {
    expect(withSize(DEFAULT_PLACEMENT, "small")).toEqual({ cx: 0.5, cy: 0.75, height: 0.42, flip: false });
    expect(withSize(DEFAULT_PLACEMENT, "large")).toEqual({ cx: 0.5, cy: 0.57, height: 0.78, flip: false });
  });
  it("withSize near the top clamps cy to 0 instead of failing", () => {
    expect(withSize({ cx: 0.3, cy: 0.2, height: 0.2, flip: false }, "large")).toEqual({ cx: 0.3, cy: 0, height: 0.78, flip: false });
  });
  it("current preset: default is center + medium; a dragged spot matches none", () => {
    expect(currentSide(DEFAULT_PLACEMENT)).toBe("center");
    expect(currentSize(DEFAULT_PLACEMENT)).toBe("medium");
    expect(currentSide({ ...DEFAULT_PLACEMENT, cx: 0.743 })).toBe("right");
    expect(currentSide({ ...DEFAULT_PLACEMENT, cx: 0.4 })).toBeNull();
    expect(currentSize({ ...DEFAULT_PLACEMENT, height: 0.5 })).toBeNull();
  });
});
