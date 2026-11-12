import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { Book } from "@/api/client";
import { editsQueueFor } from "./editsQueue";
import { placementSaverFor } from "./placementSaver";
import { ExportDrawer, ExportOptions } from "./ExportDrawer";

const book = {
  id: 1,
  title: "小兔子",
  paper_size: "a4",
  print_mode: "print_shop",
} as unknown as Book;

let summary: unknown;
let pages: unknown[];
let calls: string[];
let pdfRes: () => Response;
/** 修線／草圖寫入：等 writeGate 放行，回 writeStatus。 */
let writeGate: Promise<void> | null;
let writeStatus: number;

function json(b: unknown) {
  return new Response(JSON.stringify(b), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  pages = [];
  writeGate = null;
  writeStatus = 200;
  pdfRes = () =>
    new Response(new Blob(["pdf"]), {
      status: 200,
      headers: {
        "content-disposition": `attachment; filename="book-1.pdf"; filename*=UTF-8''${encodeURIComponent("小兔子")}.pdf`,
      },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(url);
      if (url.includes("/lineart/edits")) {
        if (writeGate) await writeGate;
        return new Response(JSON.stringify(writeStatus === 200 ? {} : { detail: "儲存失敗了" }), {
          status: writeStatus,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/share")) {
        calls.push(`body:${init?.body}`);
        return json({ ...book, shared_at: "2026-11-11T01:00:00Z" });
      }
      if (url.includes("export/summary")) return json(summary);
      if (url.includes("export.pdf")) return pdfRes();
      if (url.endsWith("/pages")) return json(pages);
      return json({});
    }),
  );
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

/** 預設測 ExportOptions 的 editor 說法；drawer：進階編輯的「匯出 PDF」抽屜（和 B 的「印出這本書」同一種）。 */
function setup({ drawer = false }: { drawer?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        {drawer ? <ExportDrawer open onClose={() => {}} book={book} /> : <ExportOptions book={book} active />}
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { spy, qc };
}

describe("ExportDrawer", () => {
  it("進階編輯的匯出抽屜和 B 的「印出這本書」一樣：預設只印可以印的頁（include=ready），請看一下的頁不印", async () => {
    summary = {
      total: 3,
      passed: 2,
      exportable_ids: [10, 11],
      ready_ids: [10],
      skipped: [{ page_id: 12, position: 2, reason: "not_passed" }],
    };
    pages = [
      { id: 10, position: 0 },
      { id: 11, position: 1 },
      { id: 12, position: 2 },
    ];
    const clicked: string[] = [];
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    setup({ drawer: true });
    await waitFor(() => expect(screen.getByTestId("passed-count")).toHaveTextContent("1"));
    expect(screen.getByTestId("passed-count").parentElement).toHaveTextContent("可以印");
    expect(screen.getByRole("radio", { name: /可以印的頁面/ })).toBeChecked();
    await waitFor(() => expect(screen.getByText(/第 2 頁/).parentElement).toHaveTextContent("請看一下"));
    await userEvent.click(screen.getByRole("button", { name: /下載 PDF（1 頁）/ }));
    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(calls.some((u) => u.endsWith("/books/1/export.pdf?include=ready"))).toBe(true);
    HTMLAnchorElement.prototype.click = orig;
  });

  it("顯示已完成／未完成數字、版面說明與不會匯出的頁面原因（不再用「通過／QA」）", async () => {
    summary = {
      total: 4,
      passed: 1,
      exportable_ids: [10],
      skipped: [
        { page_id: 11, position: 1, reason: "no_selection" },
        { page_id: 12, position: 2, reason: "not_passed" },
        { page_id: 13, position: 3, reason: "not_passed" },
      ],
    };
    pages = [
      { id: 12, needs_fix: true },
      { id: 13, needs_fix: false },
    ];
    setup();
    await waitFor(() =>
      expect(screen.getByTestId("passed-count")).toHaveTextContent("1"),
    );
    expect(screen.getByTestId("failed-count")).toHaveTextContent("3");
    expect(screen.getByTestId("passed-count").parentElement).toHaveTextContent("已完成");
    expect(screen.getByTestId("failed-count").parentElement).toHaveTextContent("未完成");
    expect(screen.getByRole("radio", { name: /只有完成的頁面/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /所有已選圖的頁面/ }).closest("label")).toHaveTextContent("含未完成，試印用");
    expect(screen.queryByText(/通過|QA/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/A4 印刷廠版面：每頁 21.6 × 30.3 cm（含 3 mm 出血）/),
    ).toBeInTheDocument();
    expect(screen.getByText(/第 2 頁/).parentElement).toHaveTextContent(
      "還沒選圖",
    );
    await waitFor(() =>
      expect(screen.getByText(/第 3 頁/).parentElement).toHaveTextContent(
        "需要修線",
      ),
    );
    expect(screen.getByText(/第 4 頁/).parentElement).toHaveTextContent(
      "未完成",
    );
  });

  it("沒有完成的頁時下載按鈕停用，提示到 ② 補線按「完成這一頁 ✓」", async () => {
    summary = {
      total: 1,
      passed: 0,
      exportable_ids: [],
      skipped: [{ page_id: 1, position: 0, reason: "not_passed" }],
    };
    setup();
    expect(await screen.findByText("沒有完成的頁面可以匯出，先在 ② 補線 按「完成這一頁 ✓」。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下載 PDF/ })).toBeDisabled();
  });

  it("include 切換改變請求網址並觸發下載（檔名取自 filename*）", async () => {
    summary = {
      total: 2,
      passed: 1,
      exportable_ids: [1],
      skipped: [{ page_id: 2, position: 1, reason: "not_passed" }],
    };
    const clicked: string[] = [];
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setup();
    await userEvent.click(
      await screen.findByRole("button", { name: /下載 PDF（1 頁）/ }),
    );
    await waitFor(() => expect(clicked).toEqual(["小兔子.pdf"]));
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(
      calls.some((u) => u.endsWith("/books/1/export.pdf?include=passed")),
    ).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");

    await userEvent.click(
      screen.getByRole("radio", { name: /所有已選圖的頁面/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /下載 PDF（2 頁）/ }),
    );
    await waitFor(() =>
      expect(calls.some((u) => u.endsWith("export.pdf?include=all"))).toBe(
        true,
      ),
    );
    HTMLAnchorElement.prototype.click = orig;
    vi.useRealTimers();
  });

  it("摘要讀取失敗時只顯示一次訊息", async () => {
    vi.mocked(fetch).mockImplementation(
      async () =>
        new Response(JSON.stringify({ detail: "讀不到" }), {
          status: 500,
          headers: { "content-type": "application/json" },
        }),
    );
    setup();
    expect(await screen.findAllByText("讀不到")).toHaveLength(1);
    expect(screen.queryByText("讀不到匯出資訊")).not.toBeInTheDocument();
  });

  it("409 顯示後端訊息", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [1], skipped: [] };
    pdfRes = () =>
      new Response(JSON.stringify({ detail: "第 1 頁：線條有缺口" }), {
        status: 409,
        headers: { "content-type": "application/json" },
      });
    setup();
    await userEvent.click(
      await screen.findByRole("button", { name: /下載 PDF/ }),
    );
    expect(await screen.findByText("第 1 頁：線條有缺口")).toBeInTheDocument();
  });

  it("載入摘要後重抓頁面與書（摘要可能把頁面退回草稿）", async () => {
    summary = { total: 0, passed: 0, exportable_ids: [], skipped: [] };
    const { spy } = setup();
    await waitFor(() =>
      expect(screen.getByTestId("passed-count")).toHaveTextContent("0"),
    );
    await waitFor(() => {
      const keys = spy.mock.calls.map((c) =>
        JSON.stringify((c[0] as { queryKey: unknown }).queryKey),
      );
      expect(keys).toContain('["pages",1]');
      expect(keys).toContain('["book",1]');
    });
  });

  // ---------- final review：匯出前等寫入 ----------

  it("下載前先等這本書的修線都寫完，再要 PDF", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [1], skipped: [] };
    const clicked: string[] = [];
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    try {
      let release!: () => void;
      writeGate = new Promise<void>((r) => (release = r));
      const { qc } = setup();
      editsQueueFor(qc, 1, 10, 7).push([{ op: "draw", width_px: 11, points: [[0.1, 0.1]] }]);
      editsQueueFor(qc, 1, 11, 9).push([{ op: "draw", width_px: 11, points: [[0.2, 0.2]] }]);
      await userEvent.click(await screen.findByRole("button", { name: /下載 PDF（1 頁）/ }));
      await act(async () => {});
      expect(calls.some((u) => u.includes("export.pdf"))).toBe(false);
      await act(async () => release());
      await waitFor(() => expect(clicked).toHaveLength(1));
      const order = calls.filter((u) => /edits|export\.pdf/.test(u));
      expect(order[order.length - 1]).toContain("export.pdf");
    } finally {
      HTMLAnchorElement.prototype.click = orig;
    }
  });

  it("有寫入失敗 → 顯示錯誤、不下載", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [1], skipped: [] };
    writeStatus = 500;
    const { qc } = setup();
    const entry = editsQueueFor(qc, 1, 10, 7);
    entry.push([{ op: "draw", width_px: 11, points: [[0.1, 0.1]] }]);
    await waitFor(() => expect(entry.failed).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: /下載 PDF（1 頁）/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/還沒存好/);
    expect(calls.some((u) => u.includes("export.pdf"))).toBe(false);
  });

  it("E4：寫入失敗的提示說是第幾頁、連到那頁的 C「去處理」", async () => {
    summary = { total: 3, passed: 1, exportable_ids: [10], ready_ids: [10], skipped: [] };
    pages = [
      { id: 9, position: 0, selected_generation_id: 6 },
      { id: 10, position: 1, selected_generation_id: 7 },
    ];
    writeStatus = 500;
    const { qc } = setup();
    await waitFor(() => expect(qc.getQueryData(["pages", 1])).toBeTruthy());
    const entry = editsQueueFor(qc, 1, 10, 7);
    entry.push([{ op: "draw", width_px: 11, points: [[0.1, 0.1]] }]);
    await waitFor(() => expect(entry.failed).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: /下載 PDF/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("第 2 頁的修線還沒存好");
    expect(screen.getByRole("link", { name: "去處理" })).toHaveAttribute("href", "/books/1/page/10");
  });

  it("E4：「重送」把失敗佇列最新的筆畫再送一次，成功後提示消失", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [10], ready_ids: [10], skipped: [] };
    pages = [{ id: 10, position: 0, selected_generation_id: 7 }];
    writeStatus = 500;
    const { qc } = setup();
    await waitFor(() => expect(qc.getQueryData(["pages", 1])).toBeTruthy());
    const entry = editsQueueFor(qc, 1, 10, 7);
    entry.push([{ op: "draw", width_px: 11, points: [[0.1, 0.1]] }]);
    await waitFor(() => expect(entry.failed).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: /下載 PDF/ }));
    await screen.findByRole("link", { name: "去處理" });
    const puts = () => calls.filter((u) => u.includes("/lineart/edits")).length;
    const before = puts();
    writeStatus = 200;
    await userEvent.click(screen.getByRole("button", { name: "重送" }));
    await waitFor(() => expect(puts()).toBe(before + 1));
    await waitFor(() => expect(screen.queryByRole("link", { name: "去處理" })).not.toBeInTheDocument());
    expect(entry.failed).toBe(false);
  });

  it("E9：匯出先等位置存檔（placementSaver）寫完，再要 PDF；beforeExport 也要先做完", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [10], ready_ids: [10], skipped: [] };
    pages = [{ id: 10, position: 0, selected_generation_id: 7 }];
    const clicked: string[] = [];
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    try {
      let releasePlacement!: () => void;
      const placementGate = new Promise<void>((r) => (releasePlacement = r));
      const base = fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string, init?: RequestInit) => {
          if (url.includes("/placement")) {
            calls.push(url);
            await placementGate;
            return json({ id: 10, book_id: 1, position: 0, selected_generation_id: 7 });
          }
          return base(url, init);
        }),
      );
      let releaseCompose!: () => void;
      const composeGate = new Promise<void>((r) => (releaseCompose = r));
      const beforeExport = vi.fn(() => composeGate);
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <ExportDrawer open onClose={() => {}} book={book} beforeExport={beforeExport} />
          </ToastProvider>
        </QueryClientProvider>,
      );
      await waitFor(() => expect(qc.getQueryData(["pages", 1])).toBeTruthy());
      placementSaverFor(qc).savePlacement(10, { cx: 0.5, cy: 0.5, height: 0.6, flip: false });
      await userEvent.click(await screen.findByRole("button", { name: /下載 PDF（1 頁）/ }));
      await act(async () => {});
      expect(beforeExport).toHaveBeenCalled();
      expect(calls.some((u) => u.includes("export.pdf"))).toBe(false);
      await act(async () => releaseCompose());
      await act(async () => {});
      expect(calls.some((u) => u.includes("export.pdf"))).toBe(false); // 位置還在送
      await act(async () => releasePlacement());
      await waitFor(() => expect(clicked).toHaveLength(1));
      const order = calls.filter((u) => /placement|export\.pdf/.test(u));
      expect(order[order.length - 1]).toContain("export.pdf");
    } finally {
      HTMLAnchorElement.prototype.click = orig;
    }
  });

  it("D8：摘要重新載入後、下載 PDF 後，都重抓 B 的 overview", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [1], ready_ids: [1], skipped: [] };
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {};
    try {
      const { spy } = setup();
      const overviewCalls = () =>
        spy.mock.calls.filter((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey) === '["overview",1]').length;
      await waitFor(() => expect(overviewCalls()).toBeGreaterThanOrEqual(1)); // 摘要載入後
      const before = overviewCalls();
      await userEvent.click(await screen.findByRole("button", { name: /下載 PDF/ }));
      await waitFor(() => expect(overviewCalls()).toBeGreaterThan(before)); // 下載後
    } finally {
      HTMLAnchorElement.prototype.click = orig;
    }
  });

  describe("分享開關", () => {
    it("沒有完成的頁時停用，並說明「至少完成一頁才能分享」", async () => {
      summary = { total: 1, passed: 0, exportable_ids: [], skipped: [{ page_id: 1, position: 0, reason: "not_passed" }] };
      setup();
      const sw = await screen.findByRole("switch", { name: "分享給其他使用者" });
      await waitFor(() => expect(screen.getByText("至少完成一頁才能分享")).toBeInTheDocument());
      expect(sw).toBeDisabled();
    });
    it("有完成的頁時可切換，呼叫 PUT /books/1/share；說明只有完成的頁面", async () => {
      summary = { total: 1, passed: 1, exportable_ids: [1], skipped: [] };
      setup();
      const sw = await screen.findByRole("switch", { name: "分享給其他使用者" });
      expect(screen.getByText(/其他使用者可以檢視並下載已完成的頁面/)).toBeInTheDocument();
      await waitFor(() => expect(sw).toBeEnabled());
      expect(sw).not.toBeChecked();
      await userEvent.click(sw);
      await waitFor(() => expect(calls).toContain("body:" + JSON.stringify({ shared: true })));
      expect(calls).toContain("/api/books/1/share");
    });
    it("已分享的書開關是開的，關閉送 shared:false", async () => {
      summary = { total: 1, passed: 1, exportable_ids: [1], skipped: [] };
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <ExportDrawer open onClose={() => {}} book={{ ...book, shared_at: "2026-11-11T01:00:00Z" } as Book} />
          </ToastProvider>
        </QueryClientProvider>,
      );
      const sw = await screen.findByRole("switch", { name: "分享給其他使用者" });
      expect(sw).toBeChecked();
      await waitFor(() => expect(sw).toBeEnabled());
      await userEvent.click(sw);
      await waitFor(() => expect(calls).toContain("body:" + JSON.stringify({ shared: false })));
    });
  });
});
