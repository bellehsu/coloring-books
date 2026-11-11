import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InviteAdmin } from "./InviteAdmin";

const notFound = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  notFound: () => notFound(),
}));

const row = (id: number, name: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  is_admin: false,
  daily_generation_cap: 60,
  today: 3,
  book_count: 2,
  last_seen: null,
  created_at: "2026-11-11T00:00:00Z",
  revoked: false,
  link: `https://books.example/i/tok${id}`,
  ...over,
});

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

function mock(list: unknown, status = 200, onPost?: (body: unknown) => Response) {
  let current = list;
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/admin/invites" && init?.method === "POST") {
      const res = onPost ? onPost(JSON.parse(String(init.body))) : json(row(9, "新的"), 201);
      if (res.ok && Array.isArray(current)) {
        // 像真的後端：之後的列表包含新建的那一筆（在最前面）
        void res.clone().json().then((created) => (current = [created, ...(current as unknown[])]));
      }
      return Promise.resolve(res);
    }
    if (url === "/api/admin/invites") return Promise.resolve(json(current, status));
    return Promise.resolve(json({ queued: 0, running: 0, comfy_ok: true }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InviteAdmin />
    </QueryClientProvider>,
  );
}
beforeEach(() => notFound.mockClear());
afterEach(() => vi.unstubAllGlobals());

describe("InviteAdmin", () => {
  it("列出邀請：欄位與連結", async () => {
    mock([row(2, "小美", { last_seen: "2026-11-11T02:00:00Z" }), row(1, "管理者", { is_admin: true, daily_generation_cap: null })]);
    setup();
    const table = await screen.findByRole("table");
    for (const h of ["名字", "身分", "每日上限", "今天", "書", "最後使用", "狀態", "連結"]) {
      expect(within(table).getByRole("columnheader", { name: h })).toBeInTheDocument();
    }
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("小美");
    expect(rows[1]).toHaveTextContent("使用者");
    expect(rows[1]).toHaveTextContent("使用中");
    expect(rows[1]).toHaveTextContent("https://books.example/i/tok2");
    expect(rows[2]).toHaveTextContent("管理者");
    expect(rows[2]).toHaveTextContent("不限");
    expect(screen.getByText(/scripts\/invites\.py/)).toBeInTheDocument();
  });

  it("勾選管理者就隱藏每日上限，並以 is_admin 送出", async () => {
    const fetchMock = mock([row(1, "舊的")], 200, (b) => json({ ...row(9, "阿華", { is_admin: true }), ...(b as object) }, 201));
    setup();
    await screen.findByRole("table");
    expect(screen.getByLabelText("每日上限")).toHaveValue(60);
    await userEvent.type(screen.getByLabelText("名字"), " 阿華 ");
    await userEvent.click(screen.getByLabelText("管理者"));
    expect(screen.queryByLabelText("每日上限")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "產生連結" }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(JSON.parse(String(post![1]!.body))).toMatchObject({ name: "阿華", is_admin: true });
    });
  });

  it("新的一列出現在最上面，並清空名字", async () => {
    mock([row(1, "舊的")], 200, () => json(row(9, "小美"), 201));
    setup();
    await screen.findByRole("table");
    await userEvent.type(screen.getByLabelText("名字"), "小美");
    await userEvent.click(screen.getByRole("button", { name: "產生連結" }));
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")[1]).toHaveTextContent("小美"));
    expect(screen.getByLabelText("名字")).toHaveValue("");
  });

  it("後端的錯誤（重複的名字）顯示在表單下面", async () => {
    mock([row(1, "舊的")], 200, () => json({ detail: "這個名字已經有邀請了" }, 409));
    setup();
    await screen.findByRole("table");
    await userEvent.type(screen.getByLabelText("名字"), "舊的");
    await userEvent.click(screen.getByRole("button", { name: "產生連結" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("這個名字已經有邀請了");
  });

  it("名字空白或上限超出範圍時不能送出", async () => {
    mock([]);
    setup();
    const submit = await screen.findByRole("button", { name: "產生連結" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("名字"), "x");
    expect(submit).toBeEnabled();
    await userEvent.clear(screen.getByLabelText("每日上限"));
    await userEvent.type(screen.getByLabelText("每日上限"), "1001");
    expect(submit).toBeDisabled();
  });

  it("「複製」寫入剪貼簿，顯示「已複製」，2 秒後恢復", async () => {
    mock([row(2, "小美")]);
    setup();
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "複製 小美 的連結" }));
    expect(writeText).toHaveBeenCalledWith("https://books.example/i/tok2");
    expect(await screen.findByText("已複製")).toBeInTheDocument();
    // 2 秒後恢復（真的等：fake timers 與 user-event／testing-library 的輪詢會互相干擾）
    await waitFor(() => expect(screen.queryByText("已複製")).not.toBeInTheDocument(), { timeout: 4000 });
    expect(screen.getByRole("button", { name: "複製 小美 的連結" })).toHaveTextContent(/^複製$/);
  });

  it("沒有連結時顯示「這個連結無法再顯示，請用指令換發」，沒有複製鈕", async () => {
    mock([row(3, "舊資料", { link: null })]);
    setup();
    expect(await screen.findByText("這個連結無法再顯示，請用指令換發")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /複製/ })).not.toBeInTheDocument();
  });

  it("已撤銷的邀請顯示「已撤銷」", async () => {
    mock([row(3, "走了", { revoked: true, link: null })]);
    setup();
    const r = (await screen.findAllByRole("row"))[1];
    expect(r).toHaveTextContent("已撤銷");
  });

  it("非管理者（後端 404）顯示一般的 404 頁", async () => {
    mock({ detail: "Not Found" }, 404);
    setup();
    await waitFor(() => expect(notFound).toHaveBeenCalled());
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
