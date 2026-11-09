import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Book } from "@/api/client";
import { BookSettingsDialog } from "./BookSettingsDialog";

const book = { id: 1, title: "小兔子", paper_size: "a4", print_mode: "home" } as unknown as Book;

function setup(onClose = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ...book }), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={qc}>
      <BookSettingsDialog open onClose={onClose} book={book} />
    </QueryClientProvider>,
  );
  return { fetchMock, spy };
}
afterEach(() => vi.unstubAllGlobals());

describe("BookSettingsDialog", () => {
  it("A4 書不能選 Letter／正方形，能選同比例的 A5", () => {
    setup();
    const letter = screen.getByRole("radio", { name: /Letter/ });
    expect(letter).toBeDisabled();
    expect(screen.getByRole("radio", { name: /正方形/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /A5/ })).toBeEnabled();
    expect(screen.getAllByText("只能改成同比例的尺寸").length).toBeGreaterThan(0);
  });

  it("改列印方式時出現提醒；只改書名不提醒", async () => {
    setup();
    expect(screen.queryByText(/會重新計算/)).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("書名"), "二");
    expect(screen.queryByText(/會重新計算/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /印刷廠/ }));
    expect(screen.getByText(/線稿會重新計算，有缺口的頁面會退回草稿，記得再跑一次自動補線/)).toBeInTheDocument();
  });

  it("送出只帶有變更的欄位，成功後關閉並重抓 pages／book／qa", async () => {
    const onClose = vi.fn();
    const { fetchMock, spy } = setup(onClose);
    await userEvent.click(screen.getByRole("radio", { name: /印刷廠/ }));
    await userEvent.click(screen.getByRole("radio", { name: /A5/ }));
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/books/1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ paper_size: "a5", print_mode: "print_shop" });
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey[0]);
    expect(keys).toEqual(expect.arrayContaining(["pages", "book", "qa"]));
  });

  it("沒有變更時儲存按鈕停用", () => {
    setup();
    expect(screen.getByRole("button", { name: "儲存" })).toBeDisabled();
  });
});
