import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "@/app/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const book = (id: number, charId: number | null) => ({
  id,
  title: `書${id}`,
  paper_size: "a4",
  print_mode: "home",
  character_generation_id: charId,
  cover_url: null,
  layout: { content_px: [2244, 3272] },
  stats: { pages: 4, passed: 1, needs_fix: 0, no_selection: 0 },
});

function mockBooks(body: unknown, status = 200) {
  const json = (b: unknown, st: number) =>
    new Response(JSON.stringify(b), { status: st, headers: { "content-type": "application/json" } });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        url === "/api/books" ? json(body, status) : json({ queued: 0, running: 0, comfy_ok: true }, 200),
      ),
    ),
  );
}
function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Home />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("我的繪本", () => {
  it("列出書並依角色狀態連結", async () => {
    mockBooks([book(1, null), book(2, 5)]);
    setup();
    expect(screen.getByRole("heading", { name: "我的繪本" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /書1/ })).toHaveAttribute("href", "/books/1/character");
    expect(screen.getByRole("link", { name: /書2/ })).toHaveAttribute("href", "/books/2/editor");
    expect(screen.getByText("2 本")).toBeInTheDocument();
    expect(screen.getAllByText("1 / 4 頁完成")).toHaveLength(2);
  });
  it("空狀態只有一個建立入口", async () => {
    mockBooks([]);
    setup();
    expect(await screen.findByText(/還沒有繪本/)).toBeInTheDocument();
  });
  it("錯誤顯示訊息", async () => {
    mockBooks({ detail: "資料庫壞了" }, 500);
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("資料庫壞了");
  });
});
