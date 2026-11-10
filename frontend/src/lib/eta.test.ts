import { describe, expect, it } from "vitest";
import { estimateSeconds, formatRemaining } from "./eta";

describe("estimateSeconds", () => {
  it("uses medians and fallbacks", () => {
    expect(estimateSeconds([{ kind: "character" }, { kind: "background" }, { kind: "pose" }], [])).toBe(65);
    const recent = [
      { kind: "pose", elapsed_s: 10 },
      { kind: "pose", elapsed_s: 100 },
      { kind: "pose", elapsed_s: 30 },
      { kind: "background", elapsed_s: 40 },
      { kind: "background", elapsed_s: 60 },
    ];
    // pose 中位數 30 ×2，background 中位數 50，character 預設 20
    expect(
      estimateSeconds([{ kind: "pose" }, { kind: "pose" }, { kind: "background" }, { kind: "character" }], recent),
    ).toBe(130);
  });
  it("only looks at the newest 10 per kind", () => {
    const recent = [
      ...Array.from({ length: 10 }, () => ({ kind: "pose", elapsed_s: 5 })),
      ...Array.from({ length: 20 }, () => ({ kind: "pose", elapsed_s: 500 })),
    ];
    expect(estimateSeconds([{ kind: "pose" }], recent)).toBe(5);
  });
  it("empty queue is 0", () => expect(estimateSeconds([], [])).toBe(0));
});

describe("formatRemaining", () => {
  it("rounds up to minutes, under a minute shows 剩不到 1 分鐘", () => {
    expect(formatRemaining(59)).toBe("剩不到 1 分鐘");
    expect(formatRemaining(60)).toBe("剩約 1 分鐘");
    expect(formatRemaining(61)).toBe("剩約 2 分鐘");
    expect(formatRemaining(120)).toBe("剩約 2 分鐘");
  });
});
