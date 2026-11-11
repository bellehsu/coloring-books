import { describe, expect, it } from "vitest";
import {
  RECOMPOSE_FAILED,
  RECOMPOSE_PENDING,
  captionSummary,
  fixHref,
  neighbours,
  pageState,
  pageTitle,
  parseFilter,
  penBlocked,
  printUrl,
  showRestore,
} from "./fixState";
import { mkPage, ovPage } from "./testData";

describe("showRestore（還原成自動畫的那張）", () => {
  it("hidden when background and pose are the auto ones and there are no user strokes", () => {
    const p = mkPage();
    expect(showRestore(p, "background")).toBe(false);
    expect(showRestore(p, "pose")).toBe(false);
    expect(showRestore(p, "lines")).toBe(false);
  });

  it("shown for the item whose choice differs from auto_*", () => {
    expect(showRestore(mkPage({ background_generation_id: 11 }), "background")).toBe(true);
    expect(showRestore(mkPage({ background_generation_id: 11 }), "pose")).toBe(false);
    expect(showRestore(mkPage({ pose_generation_id: 21 }), "pose")).toBe(true);
  });

  it("hidden when nothing was drawn automatically or nothing is selected", () => {
    expect(showRestore(mkPage({ auto_background_generation_id: null, background_generation_id: 11 }), "background")).toBe(false);
    expect(showRestore(mkPage({ pose_generation_id: null }), "pose")).toBe(false);
  });

  it("lines: shown when the user drew or erased", () => {
    expect(showRestore(mkPage({ has_user_strokes: true }), "lines")).toBe(true);
  });
});

describe("penBlocked", () => {
  it("null when no recompose is pending", () => {
    expect(penBlocked(mkPage())).toBeNull();
  });
  it("pending and failed give the backend's two messages", () => {
    expect(penBlocked(mkPage({ recompose_pending: true }))).toBe(RECOMPOSE_PENDING);
    expect(penBlocked(mkPage({ recompose_pending: true, attention: "failed" }))).toBe(RECOMPOSE_FAILED);
    expect(RECOMPOSE_PENDING).toBe("這一頁正在重新合成，請稍候再畫");
    expect(RECOMPOSE_FAILED).toBe("這一頁合成失敗，請先按「再試一次」");
  });
});

describe("neighbours（上一頁／下一頁）", () => {
  const pages = [ovPage(1, 0, "attention"), ovPage(2, 1, "ready"), ovPage(3, 2, "ready"), ovPage(4, 3, "attention")];

  it("all pages without a filter", () => {
    const { prev, next } = neighbours(pages, 2, null);
    expect(prev?.id).toBe(1);
    expect(next?.id).toBe(3);
  });

  it("attention pages only with filter=attention, from any current page", () => {
    expect(neighbours(pages, 1, "attention")).toMatchObject({ prev: null, next: { id: 4 } });
    expect(neighbours(pages, 2, "attention")).toMatchObject({ prev: { id: 1 }, next: { id: 4 } });
    expect(neighbours(pages, 4, "attention")).toMatchObject({ prev: { id: 1 }, next: null });
  });

  it("orders by position, not by list order", () => {
    const shuffled = [pages[3], pages[0], pages[2], pages[1]];
    expect(neighbours(shuffled, 3, null)).toMatchObject({ prev: { id: 2 }, next: { id: 4 } });
  });

  it("unknown page → none", () => {
    expect(neighbours(pages, 99, null)).toEqual({ prev: null, next: null });
  });
});

describe("small helpers", () => {
  it("parseFilter and fixHref keep only attention", () => {
    expect(parseFilter("attention")).toBe("attention");
    expect(parseFilter("ready")).toBeNull();
    expect(parseFilter(null)).toBeNull();
    expect(fixHref(1, 5)).toBe("/books/1/page/5");
    expect(fixHref(1, 5, "attention")).toBe("/books/1/page/5?filter=attention");
  });

  it("pageTitle and captionSummary", () => {
    expect(pageTitle(mkPage())).toBe("第 2 頁・消防車");
    expect(pageTitle(mkPage({ caption_zh: null }))).toBe("第 2 頁");
    expect(captionSummary(mkPage())).toBe("消防車・Fire Engine");
    expect(captionSummary(mkPage({ caption_zh: "", caption_en: null }))).toBe("沒有字");
  });

  it("pageState prefers the overview", () => {
    expect(pageState(mkPage(), ovPage(5, 1, "working"))).toBe("working");
    expect(pageState(mkPage(), undefined)).toBe("ready");
    expect(pageState(mkPage({ attention: "failed" }), undefined)).toBe("attention");
    expect(pageState(mkPage({ status: "draft" }), undefined)).toBe("todo");
  });

  it("printUrl changes with the line-art key and the captions", () => {
    const a = printUrl(mkPage());
    expect(a.startsWith("/api/pages/5/print.png?v=")).toBe(true);
    expect(printUrl(mkPage({ lineart_url: "/api/pages/5/lineart?v=k2" }))).not.toBe(a);
    expect(printUrl(mkPage({ caption_en: "Truck" }))).not.toBe(a);
  });
});
