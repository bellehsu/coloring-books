import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { Book } from "@/api/client";
import { ExportDrawer } from "./ExportDrawer";

const book = { id: 1, title: "小兔子", paper_size: "a4", print_mode: "print_shop" } as unknown as Book;

let summary: unknown;
let pages: unknown[];
let calls: string[];
let pdfRes: () => Response;

function json(b: unknown) {
  return new Response(JSON.stringify(b), { status: 200, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  calls = [];
  pages = [];
  pdfRes = () =>
    new Response(new Blob(["pdf"]), {
      status: 200,
      headers: { "content-disposition": `attachment; filename="book-1.pdf"; filename*=UTF-8''${encodeURIComponent("小兔子")}.pdf` },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
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

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ExportDrawer open onClose={() => {}} book={book} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { spy };
}

describe("ExportDrawer", () => {
  it("顯示通過／未通過數字、版面說明與不會匯出的頁面原因", async () => {
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
    await waitFor(() => expect(screen.getByTestId("passed-count")).toHaveTextContent("1"));
    expect(screen.getByTestId("failed-count")).toHaveTextContent("3");
    expect(screen.getByText(/A4 印刷廠版面：每頁 21.6 × 30.3 cm（含 3 mm 出血）/)).toBeInTheDocument();
    expect(screen.getByText(/第 2 頁/).parentElement).toHaveTextContent("還沒選圖");
    await waitFor(() => expect(screen.getByText(/第 3 頁/).parentElement).toHaveTextContent("需要修線"));
    expect(screen.getByText(/第 4 頁/).parentElement).toHaveTextContent("未通過 QA");
  });

  it("沒有通過頁時下載按鈕停用並顯示原因", async () => {
    summary = { total: 1, passed: 0, exportable_ids: [], skipped: [{ page_id: 1, position: 0, reason: "not_passed" }] };
    setup();
    expect(await screen.findByText(/沒有通過的頁面/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下載 PDF/ })).toBeDisabled();
  });

  it("include 切換改變請求網址並觸發下載（檔名取自 filename*）", async () => {
    summary = { total: 2, passed: 1, exportable_ids: [1], skipped: [{ page_id: 2, position: 1, reason: "not_passed" }] };
    const clicked: string[] = [];
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /下載 PDF（1 頁）/ }));
    await waitFor(() => expect(clicked).toEqual(["小兔子.pdf"]));
    expect(calls.some((u) => u.endsWith("/books/1/export.pdf?include=passed"))).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");

    await userEvent.click(screen.getByRole("radio", { name: /所有已選圖的頁面/ }));
    await userEvent.click(screen.getByRole("button", { name: /下載 PDF（2 頁）/ }));
    await waitFor(() => expect(calls.some((u) => u.endsWith("export.pdf?include=all"))).toBe(true));
    HTMLAnchorElement.prototype.click = orig;
  });

  it("409 顯示後端訊息", async () => {
    summary = { total: 1, passed: 1, exportable_ids: [1], skipped: [] };
    pdfRes = () =>
      new Response(JSON.stringify({ detail: "第 1 頁：線條有缺口" }), {
        status: 409,
        headers: { "content-type": "application/json" },
      });
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /下載 PDF/ }));
    expect(await screen.findByText("第 1 頁：線條有缺口")).toBeInTheDocument();
  });

  it("載入摘要後重抓頁面與書（摘要可能把頁面退回草稿）", async () => {
    summary = { total: 0, passed: 0, exportable_ids: [], skipped: [] };
    const { spy } = setup();
    await waitFor(() => expect(screen.getByTestId("passed-count")).toHaveTextContent("0"));
    await waitFor(() => {
      const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
      expect(keys).toContain('["pages",1]');
      expect(keys).toContain('["book",1]');
    });
  });
});
