import { describe, expect, it } from "vitest";
import { AI_PROMPT } from "./aiPrompt";

describe("AI_PROMPT", () => {
  it("has the two optional caption lines exactly as the spec words them", () => {
    const p = AI_PROMPT(3);
    expect(p).toContain("中文：（選填，印在圖下方的短字，12 字內）");
    expect(p).toContain("英文：（選填，上一行的英文，28 個字元內）");
    expect(p.indexOf("動作：（")).toBeLessThan(p.indexOf("中文：（"));
    expect(p.indexOf("中文：（")).toBeLessThan(p.indexOf("英文：（"));
    expect(p).toContain("3 頁");
  });
});
