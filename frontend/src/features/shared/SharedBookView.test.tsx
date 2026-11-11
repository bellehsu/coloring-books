import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SharedBookView } from "./SharedBookView";

const detail = {
  id: 9,
  title: "小熊歷險記",
  author: "小華",
  paper_size: "a4",
  mine: false,
  pages: [
    { id: 70, position: 0 },
    { id: 72, position: 2 },
  ],
};

function mock(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })),
    ),
  );
}
function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SharedBookView bookId={9} />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("SharedBookView", () => {
  it("顯示書名、作者、完成的頁面（依序）與下載 PDF，沒有編輯介面", async () => {
    mock(200, detail);
    setup();
    expect(await screen.findByRole("heading", { name: "小熊歷險記" })).toBeInTheDocument();
    expect(screen.getByText("作者：小華")).toBeInTheDocument();
    const imgs = screen.getAllByRole("img");
    expect(imgs.map((i) => i.getAttribute("src"))).toEqual([
      "/api/shared/pages/70/image.png",
      "/api/shared/pages/72/image.png",
    ]);
    expect(imgs[0]).toHaveAttribute("alt", "第 1 頁");
    expect(screen.getByRole("link", { name: /下載 PDF/ })).toHaveAttribute("href", "/api/shared/books/9/export.pdf");
    expect(screen.queryByRole("button", { name: /編輯|重新生成|刪除/ })).not.toBeInTheDocument();
  });

  it("404：顯示「這本繪本已經不再分享」與回首頁連結", async () => {
    mock(404, { detail: "找不到這本分享的繪本" });
    setup();
    expect(await screen.findByText("這本繪本已經不再分享")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /回首頁/ })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("link", { name: /下載 PDF/ })).not.toBeInTheDocument();
  });
});
