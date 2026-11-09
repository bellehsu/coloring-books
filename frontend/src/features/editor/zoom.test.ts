import { describe, expect, it } from "vitest";
import { ZOOM_MAX, ZOOM_MIN, clampZoom, placePaper, stepZoom, wheelZoom } from "./zoom";

describe("zoom helpers", () => {
  it("clampZoom 夾在 50%–400%，非有限值回 100%", () => {
    expect(ZOOM_MIN).toBe(0.5);
    expect(ZOOM_MAX).toBe(4);
    expect(clampZoom(0.1)).toBe(0.5);
    expect(clampZoom(9)).toBe(4);
    expect(clampZoom(1.3)).toBe(1.3);
    expect(clampZoom(Number.NaN)).toBe(1);
  });

  it("stepZoom 走固定刻度，從刻度之間也會走到下一個刻度，兩端停住", () => {
    expect(stepZoom(1, 1)).toBe(1.5);
    expect(stepZoom(1, -1)).toBe(0.75);
    expect(stepZoom(1.2, 1)).toBe(1.5);
    expect(stepZoom(1.2, -1)).toBe(1);
    expect(stepZoom(4, 1)).toBe(4);
    expect(stepZoom(0.5, -1)).toBe(0.5);
  });

  it("wheelZoom：往上捲（deltaY < 0）放大、往下縮小，並夾在範圍內", () => {
    expect(wheelZoom(1, -100)).toBeGreaterThan(1);
    expect(wheelZoom(1, 100)).toBeLessThan(1);
    expect(wheelZoom(4, -1000)).toBe(4);
    expect(wheelZoom(0.5, 1000)).toBe(0.5);
  });
});

describe("placePaper", () => {
  it("紙比視窗小 → 置中（不管 center 是多少）", () => {
    const p = placePaper({ vpW: 400, vpH: 600, dispW: 200, dispH: 300, cx: 0.1, cy: 0.9 });
    expect(p.x).toBe(100);
    expect(p.y).toBe(150);
    expect(p.cx).toBe(0.5);
    expect(p.cy).toBe(0.5);
  });

  it("紙比視窗大 → 依 center 放置，並夾住不露出紙外的舞台", () => {
    const mid = placePaper({ vpW: 400, vpH: 600, dispW: 800, dispH: 1200, cx: 0.5, cy: 0.5 });
    expect(mid.x).toBe(-200);
    expect(mid.y).toBe(-300);
    const edge = placePaper({ vpW: 400, vpH: 600, dispW: 800, dispH: 1200, cx: 0, cy: 1 });
    expect(edge.x).toBe(0); // 左邊貼齊
    expect(edge.y).toBe(-600); // 底邊貼齊
    expect(edge.cx).toBeCloseTo(0.25);
    expect(edge.cy).toBeCloseTo(0.75);
  });
});
