import { describe, expect, it } from "vitest";
import { isCleaned, isPortrait } from "./cleanup";

describe("cleanup", () => {
  it("isPortrait：位置有 frame 才是肖像模式", () => {
    expect(isPortrait({ placement: null })).toBe(false);
    expect(isPortrait({ placement: { cx: 0.5, cy: 0.5, height: 0.5, flip: false, frame: null, autofit: false } })).toBe(false);
    expect(isPortrait({ placement: { cx: 0.5, cy: 0.5, height: 0.5, flip: false, frame: { x: 0.5, y: 0.5 }, autofit: false } })).toBe(true);
  });
  it("isCleaned：source_generation_id 有值", () => {
    expect(isCleaned({ source_generation_id: null })).toBe(false);
    expect(isCleaned({ source_generation_id: 3 })).toBe(true);
  });
});
