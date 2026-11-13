import { describe, expect, it } from "vitest";
import type { BookOverview, OverviewPage } from "@/api/client";
import { cardMode, countPages, filterPages, progressOf, softNote } from "./overviewState";

const pg = (id: number, state: OverviewPage["state"], extra: Partial<OverviewPage> = {}): OverviewPage => ({
  id,
  position: id - 1,
  state,
  gap_count: null,
  caption_zh: null,
  caption_en: null,
  caption_show_zh: true,
  caption_show_en: true,
  thumb_url: null,
  lineart_url: null,
  attention_detail: null,
  review: false,
  ...extra,
});
const ov = (pages: OverviewPage[], extra: Partial<BookOverview> = {}): BookOverview => ({
  book_id: 1,
  pages,
  progress: { queued: 0, running: 0, done: 0 },
  progress_pages: { ready: pages.filter((p) => p.state === "ready").length, total: pages.length },
  estimate_s: 0,
  deferred_count: 0,
  redraw_count: 0,
  run_started_at: "2026-11-12T01:00:00Z",
  ...extra,
});

describe("countPages / filterPages", () => {
  const pages = [pg(1, "ready"), pg(2, "attention"), pg(3, "working"), pg(4, "todo"), pg(5, "ready")];
  it("counts each filter; 還沒做完 includes working and todo", () => {
    expect(countPages(pages)).toEqual({ all: 5, attention: 1, ready: 2, unfinished: 2 });
  });
  it("filters by state", () => {
    expect(filterPages(pages, "all").map((p) => p.id)).toEqual([1, 2, 3, 4, 5]);
    expect(filterPages(pages, "ready").map((p) => p.id)).toEqual([1, 5]);
    expect(filterPages(pages, "attention").map((p) => p.id)).toEqual([2]);
    expect(filterPages(pages, "unfinished").map((p) => p.id)).toEqual([3, 4]);
  });
});

describe("cardMode", () => {
  it("running while anything is queued, running or working", () => {
    expect(cardMode(ov([pg(1, "ready")], { progress: { queued: 1, running: 0, done: 2 } }))).toBe("running");
    expect(cardMode(ov([pg(1, "working")]))).toBe("running");
  });
  it("before any run, missing material means 還沒開始做", () => {
    expect(cardMode(ov([pg(1, "todo")], { run_started_at: null, deferred_count: 1 }))).toBe("notStarted");
  });
  it("a book with no pages has not started", () => {
    expect(cardMode(ov([], { run_started_at: null }))).toBe("notStarted");
  });
  it("after a run, missing material is deferred to tomorrow", () => {
    expect(cardMode(ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 }))).toBe("deferred");
  });
  it("todo pages without deferred material are unfinished", () => {
    expect(cardMode(ov([pg(1, "ready"), pg(2, "todo")]))).toBe("unfinished");
  });
  it("all ready or attention is done; an old finished book without a run is done too", () => {
    expect(cardMode(ov([pg(1, "ready"), pg(2, "attention")]))).toBe("done");
    expect(cardMode(ov([pg(1, "ready")], { run_started_at: null }))).toBe("done");
  });
});

describe("softNote", () => {
  it("gap hint on any page with gaps", () => {
    expect(softNote(pg(1, "ready", { gap_count: 3 }))).toBe("3 處線沒接起來（可以不管）");
    expect(softNote(pg(1, "ready", { gap_count: 0 }))).toBeNull();
    expect(softNote(pg(1, "ready"))).toBeNull();
  });
  it("attention shows the failure message", () => {
    expect(softNote(pg(1, "attention", { attention_detail: "角色動作沒有畫出來" }))).toBe("角色動作沒有畫出來");
    expect(softNote(pg(1, "attention"))).toBe("這一頁沒做好，點進去看看");
  });
});

describe("progressOf", () => {
  it("done over done + queued + running", () => {
    expect(progressOf(ov([], { progress: { queued: 3, running: 1, done: 4 } }))).toEqual({ done: 4, total: 8 });
  });
});
