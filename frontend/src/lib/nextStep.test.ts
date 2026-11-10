import { describe, expect, it } from "vitest";
import { nextStep } from "./nextStep";
import type { Book } from "@/api/client";

const mk = (o: Partial<Omit<Book, "stats">> & { stats?: Partial<Book["stats"]> }) =>
  ({
    character_generation_id: 1,
    ...o,
    stats: { pages: 5, passed: 5, needs_fix: 0, no_selection: 0, ...o.stats },
  }) as Book;

describe("nextStep", () => {
  it("沒角色", () => {
    expect(nextStep(mk({ character_generation_id: null }))).toEqual({ text: "下一步：設定角色", tone: "draft" });
  });
  it("沒有頁面", () => {
    expect(nextStep(mk({ stats: { pages: 0, passed: 0 } })).text).toBe("下一步：新增頁面");
  });
  it("需要修線優先", () => {
    expect(nextStep(mk({ stats: { needs_fix: 3, passed: 1 } }))).toEqual({ text: "下一步：3 頁需要修線", tone: "warn" });
  });
  it("待完成", () => {
    expect(nextStep(mk({ stats: { passed: 2 } })).text).toBe("下一步：3 頁待完成");
  });
  it("全部完成", () => {
    expect(nextStep(mk({}))).toEqual({ text: "可以匯出", tone: "ok" });
  });
});
