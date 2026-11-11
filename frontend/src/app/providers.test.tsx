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
  it("401 轉到 /invalid-link", async () => {
    vi.stubGlobal("location", { pathname: "/books/1" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(401, { detail: "連結已失效" })));
    render(<Providers>x</Providers>);
    await act(async () => {
      await api.get("/books").catch(() => {});
    });
    expect(replace).toHaveBeenCalledWith("/invalid-link");
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
