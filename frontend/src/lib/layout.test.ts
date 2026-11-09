import { describe, expect, it } from "vitest";
import { computeLayout, ratioGroup, PAPER_LABELS, formatCm } from "./layout";

describe("computeLayout（與後端 test_layout.py 同值）", () => {
  it.each([
    ["a4", "home", [2244, 3272], [832, 1216]],
    ["a4", "print_shop", [2551, 3579], [864, 1216]],
    ["a5", "home", [1512, 2244], [832, 1216]],
    ["b5", "print_shop", [2220, 3106], [864, 1216]],
    ["letter", "home", [2314, 3064], [896, 1152]],
    ["letter", "print_shop", [2621, 3371], [896, 1152]],
    ["square_21", "home", [2244, 2244], [1024, 1024]],
  ] as const)("%s %s", (size, mode, px, gen) => {
    const l = computeLayout(size, mode);
    expect(l.contentPx).toEqual(px);
    expect(l.generationSize).toEqual(gen);
  });

  it("contentMm：家用扣 10 mm 白邊、印刷廠加 3 mm 出血", () => {
    expect(computeLayout("a4", "home").contentMm).toEqual([190, 277]);
    expect(computeLayout("a4", "print_shop").contentMm).toEqual([216, 303]);
  });

  it("ratioGroup 與標籤", () => {
    expect(ratioGroup("a4")).toBe("iso");
    expect(ratioGroup("letter")).toBe("letter");
    expect(ratioGroup("square_21")).toBe("square");
    expect(PAPER_LABELS.square_21).toBe("正方形");
  });

  it("formatCm", () => {
    expect(formatCm([190, 277])).toBe("19 × 27.7 cm");
  });
});
