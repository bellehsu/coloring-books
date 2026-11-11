import { describe, expect, it } from "vitest";
import { nextStep } from "./nextStep";
import type { Book } from "@/api/client";

const mk = (o: Partial<Omit<Book, "stats">> & { stats?: Partial<Book["stats"]> }) =>
  ({
    character_generation_id: 1,
    ...o,
    stats: {
      pages: 5,
      passed: 5,
      needs_fix: 0,
      no_selection: 0,
      ready: 5,
      attention: 0,
      working: 0,
      todo: 0,
      ...o.stats,
    },
  }) as Book;

describe("nextStep（與 B 的狀態同一套說法）", () => {
  it("沒主角", () => {
    expect(nextStep(mk({ character_generation_id: null }))).toEqual({ text: "下一步：先設定主角", tone: "draft" });
  });
  it("沒有頁面", () => {
    expect(nextStep(mk({ stats: { pages: 0, passed: 0, ready: 0 } }))).toEqual({ text: "還沒開始做", tone: "draft" });
  });
  it("製作中優先", () => {
    expect(nextStep(mk({ stats: { ready: 2, working: 2, attention: 1 } }))).toEqual({ text: "製作中…", tone: "busy" });
  });
  it("請看一下（只有失敗的頁）", () => {
    expect(nextStep(mk({ stats: { ready: 3, attention: 1, todo: 1 } }))).toEqual({ text: "1 頁請你看一下", tone: "warn" });
  });
  it("還沒做完", () => {
    expect(nextStep(mk({ stats: { passed: 2, ready: 2, todo: 3 } }))).toEqual({ text: "還有 3 頁還沒做完", tone: "busy" });
  });
  it("線沒接起來（舊的 needs_fix）不算：整本做好了", () => {
    expect(nextStep(mk({ stats: { needs_fix: 2 } }))).toEqual({ text: "整本做好了，可以印", tone: "ok" });
  });
});
