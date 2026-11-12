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
  // 2 頁 PASSED，其中 1 頁「請看一下」（失敗後還是 PASSED）；needs_fix（線沒接起來）不影響書卡
  stats: { pages: 4, passed: 2, ready: 1, attention: 1, working: 0, todo: 2 },
});

function mockBooks(body: unknown, status = 200, shared: unknown = [], me: unknown = { name: "小明", is_admin: false, via_dev_owner: false }) {
  const json = (b: unknown, st: number) =>
    new Response(JSON.stringify(b), { status: st, headers: { "content-type": "application/json" } });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        url === "/api/books"
          ? json(body, status)
          : url === "/api/shared/books"
            ? json(shared, 200)
            : url === "/api/me"
              ? json(me, 200)
            : json({ queued: 0, running: 0, comfy_ok: true }, 200),
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
  it("列出書，每本都連到 B 整本書", async () => {
    mockBooks([book(1, null), book(2, 5)]);
    setup();
    expect(screen.getByRole("heading", { name: "我的繪本" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /書1/ })).toHaveAttribute("href", "/books/1");
    expect(screen.getByRole("link", { name: /書2/ })).toHaveAttribute("href", "/books/2");
    expect(screen.getByText("2 本")).toBeInTheDocument();
    expect(screen.getAllByText("1 / 4 頁可以印")).toHaveLength(2);
    // 書卡和 B 同一套說法：請看一下的頁不算做好；線沒接起來不是「需要修線」
    expect(screen.getByRole("link", { name: /書1/ })).toHaveTextContent("下一步：先設定主角");
    expect(screen.getByRole("link", { name: /書2/ })).toHaveTextContent("1 頁請你看一下");
    expect(screen.queryByText(/需要修線|頁完成|可以匯出/)).not.toBeInTheDocument();
  });
  it("空狀態只有一個建立入口", async () => {
    mockBooks([]);
    setup();
    expect(await screen.findByText(/還沒有繪本/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "建立新繪本" })).toHaveLength(1);
  });

  it("「建立新繪本」到 A 做一本新的著色書（/books/new）", async () => {
    mockBooks([book(1, 5)]);
    setup();
    await screen.findByRole("link", { name: /書1/ });
    const links = screen.getAllByRole("link", { name: "建立新繪本" });
    expect(links).toHaveLength(2);
    for (const a of links) expect(a).toHaveAttribute("href", "/books/new");
  });
  it("錯誤顯示訊息", async () => {
    mockBooks({ detail: "資料庫壞了" }, 500);
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("資料庫壞了");
  });

  it("我的繪本裡已分享的書有「已分享」徽章，沒分享的沒有", async () => {
    mockBooks([{ ...book(1, 5), shared_at: "2026-11-11T01:00:00Z" }, book(2, 5)]);
    setup();
    await screen.findByRole("link", { name: /書1/ });
    expect(screen.getAllByText("已分享")).toHaveLength(1);
    expect(screen.getByRole("link", { name: /書1/ })).toHaveTextContent("已分享");
    expect(screen.getByRole("link", { name: /書2/ })).not.toHaveTextContent("已分享");
  });
});

describe("大家的繪本", () => {
  const sharedItem = {
    id: 9,
    title: "小熊歷險記",
    author: "小華",
    paper_size: "a4",
    page_count: 3,
    cover_image_url: "/api/shared/pages/77/image.png?v=abc123",
    shared_at: "2026-11-11T01:00:00Z",
    mine: false,
  };
  it("列出別人分享的書：封面、書名、作者、頁數，連到唯讀頁", async () => {
    mockBooks([], 200, [sharedItem]);
    setup();
    expect(screen.getByRole("heading", { name: "大家的繪本" })).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: /小熊歷險記/ });
    expect(link).toHaveAttribute("href", "/shared/9");
    expect(link).toHaveTextContent("作者：小華");
    expect(link).toHaveTextContent("3 頁");
    expect(link.querySelector("img")).toHaveAttribute("src", "/api/shared/pages/77/image.png?v=abc123");
  });
  it("沒有人分享時顯示空狀態", async () => {
    mockBooks([], 200, []);
    setup();
    expect(await screen.findByText("還沒有人分享繪本。")).toBeInTheDocument();
  });
});

describe("邀請管理連結", () => {
  it("管理者看得到，一般使用者看不到", async () => {
    mockBooks([], 200, [], { name: "管理者", is_admin: true, via_dev_owner: false });
    const { unmount } = setup();
    expect(await screen.findByRole("link", { name: "邀請管理" })).toHaveAttribute("href", "/admin/invites");
    unmount();
    mockBooks([], 200, [], { name: "小明", is_admin: false, via_dev_owner: false });
    setup();
    await screen.findByText("還沒有繪本，建立第一本開始吧。");
    expect(screen.queryByRole("link", { name: "邀請管理" })).not.toBeInTheDocument();
  });

  it("本機 dev_owner 後備的 admin（沒有 cookie）看不到：後端也不開放", async () => {
    mockBooks([], 200, [], { name: "admin", is_admin: true, via_dev_owner: true });
    setup();
    await screen.findByText("還沒有繪本，建立第一本開始吧。");
    expect(screen.queryByRole("link", { name: "邀請管理" })).not.toBeInTheDocument();
  });
});
