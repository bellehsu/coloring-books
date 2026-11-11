import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { Providers } from "@/app/providers";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

function jsonRes(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockClear();
});

describe("Providers 全域錯誤處理", () => {
  it("401「連結已失效」（owner 被撤銷）→ /invalid-link?reason=revoked", async () => {
    vi.stubGlobal("location", { pathname: "/books/1" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(401, { detail: "連結已失效" })));
    render(<Providers>x</Providers>);
    await act(async () => {
      await api.get("/books").catch(() => {});
    });
    expect(replace).toHaveBeenCalledWith("/invalid-link?reason=revoked");
  });

  it("401「請用邀請連結開啟」（這個瀏覽器沒有 session）→ /invalid-link?reason=missing", async () => {
    vi.stubGlobal("location", { pathname: "/" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(401, { detail: "請用邀請連結開啟" })));
    render(<Providers>x</Providers>);
    await act(async () => {
      await api.get("/books").catch(() => {});
    });
    expect(replace).toHaveBeenCalledWith("/invalid-link?reason=missing");
  });

  it("其他 401（沒有可辨識的 detail）沿用「連結已失效」", async () => {
    vi.stubGlobal("location", { pathname: "/" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 401 })));
    render(<Providers>x</Providers>);
    await act(async () => {
      await api.get("/books").catch(() => {});
    });
    expect(replace).toHaveBeenCalledWith("/invalid-link?reason=revoked");
  });

  it("已經在 /invalid-link 就不再轉址", async () => {
    vi.stubGlobal("location", { pathname: "/invalid-link" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(401, { detail: "連結已失效" })));
    render(<Providers>x</Providers>);
    await act(async () => {
      await api.get("/books").catch(() => {});
    });
    expect(replace).not.toHaveBeenCalled();
  });
});
