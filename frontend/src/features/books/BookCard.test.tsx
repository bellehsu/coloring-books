import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Book } from "@/api/client";
import { qk } from "@/api/hooks";
import { ToastProvider } from "@/components/ui/Toast";
import { politeToasts, alertToasts } from "@/components/ui/toastTesting";
import { BookCard } from "./BookCard";

const book = {
  id: 1,
  title: "小狐狸的一天",
  mine: true,
  paper_size: "a4",
  print_mode: "home",
  shared_at: null,
  cover_url: null,
  layout: { content_px: [2480, 3508] },
  stats: { pages: 20, ready: 3 },
} as unknown as Book;

function Shelf() {
  const q = useQuery({ queryKey: qk.books(), queryFn: async () => [book], initialData: [book], staleTime: Infinity });
  return <>{q.data?.map((b) => <BookCard key={b.id} book={b} />)}</>;
}

function setup(b: Book = book, status = 204) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.books(), [b]);
  const fetchMock = vi.fn(async () =>
    status === 204
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify({ detail: "壞掉了" }), { status, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <Shelf />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { fetchMock, qc };
}
afterEach(() => vi.unstubAllGlobals());

async function openConfirm() {
  await userEvent.click(screen.getByRole("button", { name: "「小狐狸的一天」的選單" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "刪除這本書" }));
}

describe("BookCard menu", () => {
  it("shows the ⋯ menu only on own books", () => {
    setup({ ...book, mine: false } as Book);
    expect(screen.queryByRole("button", { name: "「小狐狸的一天」的選單" })).toBeNull();
  });

  it("the menu has one item and Esc closes it", async () => {
    setup();
    const btn = screen.getByRole("button", { name: "「小狐狸的一天」的選單" });
    expect(btn).toHaveAttribute("aria-haspopup", "menu");
    await userEvent.click(btn);
    expect(screen.getAllByRole("menuitem")).toHaveLength(1);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("confirm dialog has the exact copy", async () => {
    setup();
    await openConfirm();
    expect(screen.getByRole("dialog", { name: "確定要刪除「小狐狸的一天」嗎？" })).toBeInTheDocument();
    expect(screen.getByText("20 頁、所有圖和修過的線都會刪掉，刪了就救不回來。分享出去的連結也會失效。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刪除" })).toBeInTheDocument();
  });

  it("取消 closes without a request", async () => {
    const { fetchMock } = setup();
    await openConfirm();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("刪除 sends DELETE and drops the card", async () => {
    const { fetchMock, qc } = setup();
    await openConfirm();
    await userEvent.click(screen.getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(qc.getQueryData<Book[]>(qk.books())).toEqual([]));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/books/1");
    expect(init.method).toBe("DELETE");
    expect(politeToasts()).toHaveTextContent("已刪除「小狐狸的一天」");
  });

  it("a failed delete keeps the card and shows the error toast", async () => {
    const { qc } = setup(book, 500);
    await openConfirm();
    await userEvent.click(screen.getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(alertToasts()).toHaveTextContent("壞掉了"));
    expect(qc.getQueryData<Book[]>(qk.books())).toHaveLength(1);
  });
});
