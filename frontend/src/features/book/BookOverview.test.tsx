import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { BookOverview as Overview, OverviewPage, ProducePlan } from "@/api/client";
import { BookOverview } from "./BookOverview";

const book = {
  id: 1,
  title: "小兔子的交通工具書",
  paper_size: "a4",
  print_mode: "home",
  character_generation_id: 9,
  cover_url: null,
  shared_at: null,
  layout: { content_px: [2244, 3272] },
  stats: { pages: 3, passed: 2, needs_fix: 0, no_selection: 0 },
};

const pg = (id: number, state: OverviewPage["state"], extra: Partial<OverviewPage> = {}): OverviewPage => ({
  id,
  position: id - 1,
  state,
  gap_count: null,
  caption_zh: `第${id}張`,
  caption_en: null,
  thumb_url: `/api/generations/${100 + id}/image`,
  lineart_url: `/api/pages/${id}/lineart?v=k${id}`,
  attention_detail: null,
  ...extra,
});
const ov = (pages: OverviewPage[], extra: Partial<Overview> = {}): Overview => ({
  book_id: 1,
  pages,
  progress: { queued: 0, running: 0, done: 6 },
  estimate_s: 0,
  deferred_count: 0,
  run_started_at: "2026-11-12T01:00:00Z",
  ...extra,
});

let overview: Overview;
let plan: ProducePlan;
let produceRes: () => Response;
let summary: unknown;
let calls: { url: string; method: string }[];

function json(b: unknown, status = 200) {
  return new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  calls = [];
  overview = ov([
    pg(1, "attention", { attention_detail: "角色動作沒有畫出來" }),
    pg(2, "ready", { gap_count: 3 }),
    pg(3, "ready"),
  ]);
  plan = { backgrounds: 0, poses: 0, total: 0, estimate_s: 0, remaining: 5, fits: 0 };
  produceRes = () => json({ queued: [11], deferred_pages: [], pipeline_pages: [] }, 202);
  summary = {
    total: 3,
    passed: 2,
    exportable_ids: [2, 3],
    ready_ids: [2, 3],
    skipped: [{ page_id: 1, position: 0, reason: "not_passed" }],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url === "/api/books/1") return json(book);
      if (url === "/api/books/1/overview") return json(overview);
      if (url === "/api/books/1/produce/plan") return json(plan);
      if (url === "/api/books/1/produce" && method === "POST") return produceRes();
      if (url === "/api/books/1/export/summary") return json(summary);
      if (url.startsWith("/api/books/1/export.pdf"))
        return new Response(new Blob(["%PDF"]), {
          status: 200,
          headers: { "content-type": "application/pdf", "content-disposition": 'attachment; filename="book-1.pdf"' },
        });
      if (url === "/api/books/1/pages") return json(overview.pages.map((p) => ({ id: p.id, position: p.position, needs_fix: false })));
      return json({ queued: 0, running: 0, comfy_ok: true });
    }),
  );
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.title = "";
});

function setup() {
  // 同 app/providers.tsx：全域關掉 refetchOnWindowFocus（要的 query 自己打開）
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BookOverview bookId={1} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const produced = () => calls.filter((c) => c.url === "/api/books/1/produce" && c.method === "POST").length;

describe("B 整本書：頁首與頁面卡", () => {
  it("shows the title, header links and one card per page linking to C", async () => {
    setup();
    expect(await screen.findByRole("heading", { level: 1, name: "小兔子的交通工具書" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "主角與故事" })).toHaveAttribute("href", "/books/1/setup");
    expect(screen.getByRole("link", { name: "進階編輯" })).toHaveAttribute("href", "/books/1/editor");
    expect(screen.getByRole("button", { name: "印出這本書" })).toBeInTheDocument();

    const list = await screen.findByRole("list", { name: "頁面" });
    const cards = within(list).getAllByRole("link");
    expect(cards.map((a) => a.getAttribute("href"))).toEqual(["/books/1/page/1", "/books/1/page/2", "/books/1/page/3"]);
    expect(cards[0]).toHaveTextContent("請看一下");
    expect(cards[0]).toHaveTextContent("角色動作沒有畫出來");
    expect(cards[1]).toHaveTextContent("可以印 ✓");
    expect(cards[1]).toHaveTextContent("3 處線沒接起來（可以不管）");
    expect(cards[1]).toHaveTextContent("第2張");
    expect(within(cards[1]).getByRole("img", { name: "第 2 頁" })).toHaveAttribute("src", "/api/pages/2/lineart?v=k2");
  });

  it("working and todo pages show 製作中 and 還沒做完", async () => {
    overview = ov([pg(1, "working"), pg(2, "todo", { lineart_url: null, thumb_url: null })], {
      progress: { queued: 1, running: 1, done: 0 },
    });
    setup();
    const list = await screen.findByRole("list", { name: "頁面" });
    const cards = within(list).getAllByRole("link");
    expect(cards[0]).toHaveTextContent("製作中");
    expect(cards[1]).toHaveTextContent("還沒做完");
    expect(within(cards[1]).queryByRole("img")).not.toBeInTheDocument();
  });
});

describe("B 整本書：狀態卡", () => {
  it("done: 整本做好了！, per-page bar, counts and 去看那一頁 →", async () => {
    setup();
    const card = await screen.findByRole("region", { name: "整本做好了！" });
    expect(within(card).getByRole("list", { name: "每一頁的狀態" }).children).toHaveLength(3);
    expect(card).toHaveTextContent("2 頁可以印・1 頁請你看一下");
    expect(within(card).getByRole("link", { name: "去看那一頁 →" })).toHaveAttribute("href", "/books/1/page/1?filter=attention");
  });

  it("done without attention has no 去看那一頁", async () => {
    overview = ov([pg(1, "ready"), pg(2, "ready")]);
    setup();
    const card = await screen.findByRole("region", { name: "整本做好了！" });
    expect(card).toHaveTextContent("2 頁可以印");
    expect(within(card).queryByRole("link", { name: "去看那一頁 →" })).not.toBeInTheDocument();
  });

  it("running: progress and 可以先去忙，好了會通知你", async () => {
    overview = ov([pg(1, "working"), pg(2, "ready")], { progress: { queued: 2, running: 1, done: 3 }, estimate_s: 66 });
    setup();
    const card = await screen.findByRole("region", { name: "正在做這本書…" });
    expect(card).toHaveTextContent("可以先去忙，好了會通知你");
    expect(card).toHaveTextContent("剩約 2 分鐘");
    const bar = within(card).getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuemax", "6");
  });

  it("before any run: 還沒開始做 and 開始做這本書 calls produce, never 明天繼續", async () => {
    overview = ov([pg(1, "todo"), pg(2, "todo")], { run_started_at: null, deferred_count: 2, progress: { queued: 0, running: 0, done: 0 } });
    setup();
    const card = await screen.findByRole("region", { name: "還沒開始做" });
    expect(screen.queryByText(/明天繼續/)).not.toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "開始做這本書" }));
    await waitFor(() => expect(produced()).toBe(1));
  });

  it("before any run without a character: 開始做這本書 goes to setup", async () => {
    overview = ov([], { run_started_at: null, progress: { queued: 0, running: 0, done: 0 } });
    book.character_generation_id = null as unknown as number;
    try {
      setup();
      const card = await screen.findByRole("region", { name: "還沒開始做" });
      await waitFor(() =>
        expect(within(card).getByRole("link", { name: "開始做這本書" })).toHaveAttribute("href", "/books/1/setup"),
      );
    } finally {
      book.character_generation_id = 9;
    }
  });

  it("after a run: 還有 N 頁明天繼續 and 繼續做 calls produce", async () => {
    overview = ov([pg(1, "ready"), pg(2, "todo"), pg(3, "todo")], { deferred_count: 2 });
    setup();
    const card = await screen.findByRole("region", { name: "還有 2 頁明天繼續" });
    expect(card).toHaveTextContent("1 頁可以印・2 頁還沒做完");
    const go = within(card).getByRole("button", { name: "繼續做" });
    await waitFor(() => expect(go).toBeEnabled());
    await userEvent.click(go);
    await waitFor(() => expect(produced()).toBe(1));
  });

  it("繼續做 is disabled when nothing is left today", async () => {
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    plan = { ...plan, total: 2, remaining: 0, fits: 0 };
    setup();
    const card = await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    await waitFor(() => expect(within(card).getByRole("button", { name: "繼續做" })).toBeDisabled());
    expect(card).toHaveTextContent("今天的生成次數已用完，明天再來");
  });

  it("繼續做 comes back when the tab regains focus the next day", async () => {
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    plan = { ...plan, total: 2, remaining: 0, fits: 0 };
    setup();
    const card = await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    await waitFor(() => expect(within(card).getByRole("button", { name: "繼續做" })).toBeDisabled());
    plan = { ...plan, remaining: 5, fits: 2 };
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(within(card).getByRole("button", { name: "繼續做" })).toBeEnabled());
    expect(card).not.toHaveTextContent("今天的生成次數已用完");
  });

  it("re-checks the plan every minute while nothing is left today, and stops once there is", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    plan = { ...plan, total: 2, remaining: 0, fits: 0 };
    setup();
    const card = await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    await waitFor(() => expect(within(card).getByRole("button", { name: "繼續做" })).toBeDisabled());
    const plans = () => calls.filter((c) => c.url === "/api/books/1/produce/plan").length;
    const before = plans();
    plan = { ...plan, remaining: 5, fits: 2 };
    await act(() => vi.advanceTimersByTimeAsync(61_000));
    expect(plans()).toBe(before + 1);
    await waitFor(() => expect(within(card).getByRole("button", { name: "繼續做" })).toBeEnabled());
    await act(() => vi.advanceTimersByTimeAsync(180_000));
    expect(plans()).toBe(before + 1);
  });

  it("a 429 refetches the plan, so 繼續做 turns off and the quota note shows", async () => {
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    produceRes = () => {
      plan = { ...plan, total: 2, remaining: 0, fits: 0 };
      return json({ detail: "今天的生成次數已用完，明天再來" }, 429);
    };
    setup();
    const card = await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    const go = within(card).getByRole("button", { name: "繼續做" });
    await waitFor(() => expect(go).toBeEnabled());
    await userEvent.click(go);
    await waitFor(() => expect(within(card).getByRole("button", { name: "繼續做" })).toBeDisabled());
    expect(card).toHaveTextContent("今天的生成次數已用完，明天再來");
  });

  it("429 from produce shows the toast", async () => {
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    produceRes = () => json({ detail: "今天的生成次數已用完，明天再來" }, 429);
    setup();
    const card = await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    const go = within(card).getByRole("button", { name: "繼續做" });
    await waitFor(() => expect(go).toBeEnabled());
    await userEvent.click(go);
    expect(await screen.findByRole("alert")).toHaveTextContent("今天的生成次數已用完，明天再來");
  });
});

describe("B 整本書：篩選", () => {
  it("每個篩選都有數量，按下只顯示那些頁", async () => {
    overview = ov([pg(1, "attention"), pg(2, "ready"), pg(3, "ready"), pg(4, "todo")], { deferred_count: 0 });
    setup();
    const filters = await screen.findByRole("group", { name: "篩選頁面" });
    const all = within(filters).getByRole("button", { name: "全部 4" });
    expect(all).toHaveAttribute("aria-pressed", "true");
    within(filters).getByRole("button", { name: "請看一下 1" });
    within(filters).getByRole("button", { name: "還沒做完 1" });
    await userEvent.click(within(filters).getByRole("button", { name: "可以印 2" }));
    expect(within(filters).getByRole("button", { name: "可以印 2" })).toHaveAttribute("aria-pressed", "true");
    const cards = within(screen.getByRole("list", { name: "頁面" })).getAllByRole("link");
    expect(cards.map((a) => a.getAttribute("href"))).toEqual(["/books/1/page/2", "/books/1/page/3"]);
    await userEvent.click(within(filters).getByRole("button", { name: "還沒做完 1" }));
    expect(within(screen.getByRole("list", { name: "頁面" })).getAllByRole("link")).toHaveLength(1);
    // 請看一下：C 的上一頁／下一頁只在要看的頁之間跳
    await userEvent.click(within(filters).getByRole("button", { name: "請看一下 1" }));
    const attention = within(screen.getByRole("list", { name: "頁面" })).getAllByRole("link");
    expect(attention.map((a) => a.getAttribute("href"))).toEqual(["/books/1/page/1?filter=attention"]);
  });
});

describe("B 整本書：輪詢", () => {
  it("polls while working; when it finishes shows 整本做好了！ toast and ✓ 做好了 title", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    overview = ov([pg(1, "working"), pg(2, "ready")], { progress: { queued: 0, running: 1, done: 1 } });
    setup();
    await screen.findByRole("region", { name: "正在做這本書…" });
    const before = calls.filter((c) => c.url === "/api/books/1/overview").length;
    overview = ov([pg(1, "ready"), pg(2, "ready")], { progress: { queued: 0, running: 0, done: 2 } });
    await act(() => vi.advanceTimersByTimeAsync(3100));
    await screen.findByRole("region", { name: "整本做好了！" });
    expect(calls.filter((c) => c.url === "/api/books/1/overview").length).toBeGreaterThan(before);
    expect(screen.getByRole("status")).toHaveTextContent("整本做好了！");
    expect(document.title).toContain("✓ 做好了");

    // 做完就不再輪詢
    const after = calls.filter((c) => c.url === "/api/books/1/overview").length;
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(calls.filter((c) => c.url === "/api/books/1/overview").length).toBe(after);
  });

  it("keeps polling in a hidden tab, so the ✓ 做好了 title appears while the user is elsewhere", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    overview = ov([pg(1, "working"), pg(2, "ready")], { progress: { queued: 0, running: 1, done: 1 } });
    setup();
    await screen.findByRole("region", { name: "正在做這本書…" });
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    try {
      document.dispatchEvent(new Event("visibilitychange"));
      const before = calls.filter((c) => c.url === "/api/books/1/overview").length;
      overview = ov([pg(1, "ready"), pg(2, "ready")], { progress: { queued: 0, running: 0, done: 2 } });
      await act(() => vi.advanceTimersByTimeAsync(3100));
      expect(calls.filter((c) => c.url === "/api/books/1/overview").length).toBeGreaterThan(before);
      await waitFor(() => expect(document.title).toContain("✓ 做好了"));
    } finally {
      vis.mockRestore();
    }
  });

  it("a run that finishes with deferred pages says so in the toast", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    overview = ov([pg(1, "working"), pg(2, "todo")], { progress: { queued: 1, running: 0, done: 0 } });
    setup();
    await screen.findByRole("region", { name: "正在做這本書…" });
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    await act(() => vi.advanceTimersByTimeAsync(3100));
    await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    expect(screen.getByRole("status")).toHaveTextContent("今天的做好了，還有 1 頁明天繼續");
  });

  it("繼續做 whose work finishes before the next poll still toasts", async () => {
    overview = ov([pg(1, "ready"), pg(2, "todo")], { deferred_count: 1 });
    produceRes = () => {
      overview = ov([pg(1, "ready"), pg(2, "ready")], { progress: { queued: 0, running: 0, done: 2 } });
      return json({ queued: [], deferred_pages: [], pipeline_pages: [2] }, 202);
    };
    setup();
    const card = await screen.findByRole("region", { name: "還有 1 頁明天繼續" });
    const go = within(card).getByRole("button", { name: "繼續做" });
    await waitFor(() => expect(go).toBeEnabled());
    await userEvent.click(go);
    await screen.findByRole("region", { name: "整本做好了！" });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("整本做好了！"));
    expect(document.title).toContain("✓ 做好了");
  });

  it("does not toast when the book was already done on load", async () => {
    setup();
    await screen.findByRole("region", { name: "整本做好了！" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(document.title).not.toContain("✓ 做好了");
  });
});

describe("B 整本書：印出這本書", () => {
  it("先看一眼可以印的頁面，請看一下的也列出來，再到 PDF 選項", async () => {
    setup();
    await screen.findByRole("list", { name: "頁面" });
    await userEvent.click(screen.getByRole("button", { name: "印出這本書" }));
    const sheet = screen.getByRole("dialog", { name: "印出這本書" });
    const glance = within(sheet).getByRole("list", { name: "會印出來的頁面" });
    expect(within(glance).getAllByRole("img").map((i) => i.getAttribute("alt"))).toEqual(["第 2 頁", "第 3 頁"]);
    const attention = within(sheet).getByRole("list", { name: "請看一下的頁面" });
    expect(attention).toHaveTextContent("第 1 頁");
    expect(attention).toHaveTextContent("角色動作沒有畫出來");
    const include = within(sheet).getByRole("checkbox", { name: "這些頁也一起印（試印）" });
    expect(include).not.toBeChecked();
    expect(within(sheet).queryByRole("button", { name: /下載 PDF/ })).not.toBeInTheDocument();

    await userEvent.click(within(sheet).getByRole("button", { name: "看好了，下一步 →" }));
    expect(await within(sheet).findByRole("button", { name: "下載 PDF（2 頁）" })).toBeInTheDocument();
    expect(within(sheet).getByRole("radio", { name: /可以印的頁面/ })).toBeChecked();
    await userEvent.click(within(sheet).getByRole("button", { name: "← 回去看一眼" }));
    expect(within(sheet).getByRole("list", { name: "會印出來的頁面" })).toBeInTheDocument();
  });

  it("勾選一起印 → PDF 選項預選所有已選圖的頁面", async () => {
    setup();
    await screen.findByRole("list", { name: "頁面" });
    await userEvent.click(screen.getByRole("button", { name: "印出這本書" }));
    const sheet = screen.getByRole("dialog", { name: "印出這本書" });
    await userEvent.click(within(sheet).getByRole("checkbox", { name: "這些頁也一起印（試印）" }));
    await userEvent.click(within(sheet).getByRole("button", { name: "看好了，下一步 →" }));
    await within(sheet).findByRole("button", { name: /下載 PDF/ });
    expect(within(sheet).getByRole("radio", { name: /所有已經有圖的頁面/ })).toBeChecked();
  });

  it("請看一下但還是 PASSED 的頁（換一張失敗）：看一眼不算它，預設的 PDF 也不印它", async () => {
    overview = ov([pg(1, "attention", { attention_detail: "圖片產生失敗" }), pg(2, "ready"), pg(3, "ready")]);
    summary = { total: 3, passed: 3, exportable_ids: [1, 2, 3], ready_ids: [2, 3], skipped: [] };
    const createObjectURL = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    setup();
    await screen.findByRole("list", { name: "頁面" });
    await userEvent.click(screen.getByRole("button", { name: "印出這本書" }));
    const sheet = screen.getByRole("dialog", { name: "印出這本書" });
    await waitFor(() => expect(sheet).toHaveTextContent("這 2 頁會印出來。"));
    const glance = within(sheet).getByRole("list", { name: "會印出來的頁面" });
    expect(within(glance).getAllByRole("img").map((i) => i.getAttribute("alt"))).toEqual(["第 2 頁", "第 3 頁"]);
    expect(within(sheet).getByRole("list", { name: "請看一下的頁面" })).toHaveTextContent("第 1 頁");

    await userEvent.click(within(sheet).getByRole("button", { name: "看好了，下一步 →" }));
    const dl = await within(sheet).findByRole("button", { name: "下載 PDF（2 頁）" });
    expect(within(sheet).getByRole("list", { name: "不會印的頁面" })).toHaveTextContent("第 1 頁請看一下");
    await userEvent.click(dl);
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/books/1/export.pdf"))).toBe(true));
    expect(calls.filter((c) => c.url.startsWith("/api/books/1/export.pdf")).map((c) => c.url)).toEqual([
      "/api/books/1/export.pdf?include=ready",
    ]);
  });

  it("勾了一起印：看一眼說一共印幾頁，與 PDF 的頁數一樣", async () => {
    overview = ov([pg(1, "attention", { attention_detail: "圖片產生失敗" }), pg(2, "ready"), pg(3, "todo", { lineart_url: null, thumb_url: null })]);
    summary = {
      total: 3,
      passed: 2,
      exportable_ids: [1, 2],
      ready_ids: [2],
      skipped: [{ page_id: 3, position: 2, reason: "no_selection" }],
    };
    setup();
    await screen.findByRole("list", { name: "頁面" });
    await userEvent.click(screen.getByRole("button", { name: "印出這本書" }));
    const sheet = screen.getByRole("dialog", { name: "印出這本書" });
    await waitFor(() => expect(sheet).toHaveTextContent("這 1 頁會印出來。"));
    expect(sheet).toHaveTextContent("還有 1 頁還沒做完，不會印出來。");
    await userEvent.click(within(sheet).getByRole("checkbox", { name: "這些頁也一起印（試印）" }));
    expect(sheet).toHaveTextContent("加上請看一下的頁，一共印 2 頁。");
    await userEvent.click(within(sheet).getByRole("button", { name: "看好了，下一步 →" }));
    expect(await within(sheet).findByRole("button", { name: "下載 PDF（2 頁）" })).toBeInTheDocument();
  });
});
