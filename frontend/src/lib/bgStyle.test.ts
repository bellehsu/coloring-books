import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BG_STYLE_KEY, effectiveBgStyle, readBgStyle, writeBgStyle } from "./bgStyle";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("bgStyle storage", () => {
  it("defaults to simple", () => {
    expect(readBgStyle()).toBe("simple");
  });

  it("remembers detailed under coloring.bgStyle", () => {
    writeBgStyle("detailed");
    expect(BG_STYLE_KEY).toBe("coloring.bgStyle");
    expect(localStorage.getItem("coloring.bgStyle")).toBe("detailed");
    expect(readBgStyle()).toBe("detailed");
    writeBgStyle("simple");
    expect(readBgStyle()).toBe("simple");
  });

  it("an unknown stored value reads as simple", () => {
    localStorage.setItem("coloring.bgStyle", "fancy");
    expect(readBgStyle()).toBe("simple");
  });

  it("storage that throws reads as simple and writing does not throw", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(readBgStyle()).toBe("simple");
    expect(() => writeBgStyle("detailed")).not.toThrow();
  });
});

describe("effectiveBgStyle", () => {
  it.each([
    // stored, hasSketch, simpleAvailable → effective
    ["simple", false, undefined, "simple"],
    ["simple", false, true, "simple"],
    ["simple", false, false, "detailed"],
    ["simple", true, undefined, "detailed"],
    ["simple", true, true, "detailed"],
    ["simple", true, false, "detailed"],
    ["detailed", false, undefined, "detailed"],
    ["detailed", false, true, "detailed"],
    ["detailed", false, false, "detailed"],
    ["detailed", true, true, "detailed"],
  ] as const)("%s, sketch=%s, simpleAvailable=%s → %s", (stored, hasSketch, simpleAvailable, want) => {
    expect(effectiveBgStyle(stored, { hasSketch, simpleAvailable })).toBe(want);
  });
});
