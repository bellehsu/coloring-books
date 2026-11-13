import { describe, expect, it } from "vitest";
import type { BookOverview, OverviewPage } from "@/api/client";
import { cardMode, countPages, countsLine, filterPages, inAttention, progressPages, softNote } from "./overviewState";

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

describe("progressPages", () => {
  it("progressPages reads progress_pages", () => {
    const o = ov([pg(1, "ready"), pg(2, "working")], {
      progress: { queued: 3, running: 1, done: 4 },
      progress_pages: { ready: 1, total: 2 },
    });
    expect(progressPages(o)).toEqual({ ready: 1, total: 2 });
  });
});

describe("review pages (soft 請看一下)", () => {
  it("inAttention: review pages are in both 請看一下 and 可以印", () => {
    const review = pg(2, "ready", { review: true, attention_detail: "請看一下：換了新的主角動作" });
    const pages = [pg(1, "attention"), review, pg(3, "ready")];
    expect(inAttention(pages[0])).toBe(true);
    expect(inAttention(review)).toBe(true);
    expect(inAttention(pages[2])).toBe(false);
    expect(filterPages(pages, "attention").map((p) => p.id)).toEqual([1, 2]);
    expect(filterPages(pages, "ready").map((p) => p.id)).toEqual([2, 3]);
    expect(countPages(pages)).toEqual({ all: 3, attention: 2, ready: 2, unfinished: 0 });
    expect(softNote(review)).toBe("請看一下：換了新的主角動作");
  });

  it("a review page that is working again is not in 請看一下 (soft tag only when ready)", () => {
    const working = pg(2, "working", { review: true, attention_detail: "請看一下：換了新的主角動作" });
    const pages = [pg(1, "ready"), working];
    expect(inAttention(working)).toBe(false);
    expect(filterPages(pages, "attention")).toEqual([]);
    expect(filterPages(pages, "unfinished").map((p) => p.id)).toEqual([2]);
    expect(countPages(pages)).toEqual({ all: 2, attention: 0, ready: 1, unfinished: 1 });
  });

  it("softNote shows the review note only when the page is ready", () => {
    const detail = "請看一下：換了新的主角動作";
    expect(softNote(pg(1, "ready", { review: true, attention_detail: detail }))).toBe(detail);
    expect(softNote(pg(2, "working", { review: true, attention_detail: detail }))).toBeNull();
    expect(softNote(pg(3, "working", { review: true, attention_detail: detail, gap_count: 2 }))).toBe("2 處線沒接起來（可以不管）");
  });
});

describe("countsLine", () => {
  it("a review page counts once, as 可以印, so the parts add up to the page count", () => {
    const pages = [pg(1, "ready", { review: true }), pg(2, "ready"), pg(3, "attention"), pg(4, "todo")];
    expect(countsLine(pages)).toBe("2 頁可以印・1 頁請你看一下・1 頁還沒做完");
    expect(countsLine([pg(1, "ready", { review: true })])).toBe("1 頁可以印");
    // 篩選的「請看一下」仍然包含它
    expect(countPages(pages).attention).toBe(2);
  });
});

describe("cardMode redraw", () => {
  it("cardMode redraw when redraw_count > 0 and nothing is running", () => {
    expect(cardMode(ov([pg(1, "ready"), pg(2, "ready")], { redraw_count: 1 }))).toBe("redraw");
    expect(cardMode(ov([pg(1, "ready"), pg(2, "todo")], { redraw_count: 1, deferred_count: 1 }))).toBe("redraw");
    expect(cardMode(ov([pg(1, "working")], { redraw_count: 1 }))).toBe("running");
  });
});
