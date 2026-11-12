import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import SwitchAccountPage from "@/app/switch-account/page";

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

function setup(hash: string, peekStatus = 200) {
  const assign = vi.fn();
  vi.stubGlobal("location", { hash, assign, pathname: "/switch-account" });
  const fetchMock = vi.fn((...[url]: [string, RequestInit?]) =>
    Promise.resolve(
      url === "/api/me"
        ? json({ name: "小明", is_admin: false, via_dev_owner: false })
        : url === "/api/invites/peek"
          ? peekStatus === 200
            ? json({ name: "阿華" })
            : json({ detail: "x" }, peekStatus)
          : url === "/api/invites/accept"
            ? json({ name: "阿華" })
            : json({}, 404),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SwitchAccountPage />
    </QueryClientProvider>,
  );
  return { assign, fetchMock };
}
const acceptCalls = (f: ReturnType<typeof vi.fn>) => f.mock.calls.filter((c) => c[0] === "/api/invites/accept");

afterEach(() => vi.unstubAllGlobals());

describe("/switch-account", () => {
  it("顯示邀請的名字與目前的名字；peek 用 header 帶 token", async () => {
    const { fetchMock } = setup("#tok123");
    expect(
      await screen.findByText("這個連結是「阿華」的。要切換成「阿華」嗎？目前是「小明」。"),
    ).toBeInTheDocument();
    const peek = fetchMock.mock.calls.find((c) => c[0] === "/api/invites/peek")!;
    expect((peek[1] as RequestInit).headers).toEqual({ "X-Invite-Token": "tok123" });
  });

  it("切換：POST accept 之後整頁回首頁", async () => {
    const { assign, fetchMock } = setup("#tok123");
    await userEvent.click(await screen.findByRole("button", { name: "切換" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    const calls = acceptCalls(fetchMock);
    expect(calls).toHaveLength(1);
    expect(JSON.parse((calls[0][1] as RequestInit).body as string)).toEqual({ token: "tok123" });
  });

  it("留在目前的身分：回首頁，不呼叫 accept", async () => {
    const { assign, fetchMock } = setup("#tok123");
    await userEvent.click(await screen.findByRole("button", { name: "留在目前的身分" }));
    expect(assign).toHaveBeenCalledWith("/");
    expect(acceptCalls(fetchMock)).toHaveLength(0);
  });

  it("沒有 token 或 peek 404：這個連結已經失效，附回首頁連結", async () => {
    for (const [hash, status] of [["", 200], ["#bad", 404]] as const) {
      const { unmount } = (() => {
        setup(hash, status);
        return { unmount: () => document.body.replaceChildren() };
      })();
      expect(await screen.findByText("這個連結已經失效")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "回首頁" })).toHaveAttribute("href", "/");
      expect(screen.queryByRole("button", { name: "切換" })).toBeNull();
      unmount();
    }
  });
});
