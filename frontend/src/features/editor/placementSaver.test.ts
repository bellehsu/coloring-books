import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_PLACEMENT } from "@/api/client";
import { hasPendingWrites } from "./pendingWrites";
import { forgetDeletedPage, forgetPageId } from "./forgetPage";
import { placementSaverFor } from "./placementSaver";

const tick = () => new Promise((r) => setTimeout(r, 0));
const res = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let gates: { url: string; method: string; body: unknown; release: (status?: number) => void }[];

beforeEach(() => {
  gates = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string, init: RequestInit = {}) =>
        new Promise<Response>((r) =>
          gates.push({
            url,
            method: init.method ?? "GET",
            body: init.body ? JSON.parse(init.body as string) : undefined,
            release: (status = 200) => r(res(status, status === 200 ? { id: 1, book_id: 7 } : { detail: "壞了" })),
          }),
        ),
    ),
  );
});
afterEach(() => vi.unstubAllGlobals());

const at = (cx: number) => ({ ...DEFAULT_PLACEMENT, cx });

describe("placementSaverFor", () => {
  it("同一個 QueryClient 共用一個 saver", () => {
    const qc = new QueryClient();
    expect(placementSaverFor(qc)).toBe(placementSaverFor(qc));
  });

  it("連續 5 次只送第一次與最新一次，PUT placement；whenIdle 送完才 resolve", async () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const s = placementSaverFor(qc);
    for (let i = 1; i <= 5; i++) s.savePlacement(1, at(i / 10));
    expect(s.busy()).toBe(true);
    let idle = false;
    void s.whenIdle(1).then(() => (idle = true));
    await tick();
    expect(gates).toHaveLength(1);
    expect(gates[0].url).toBe("/api/pages/1/placement");
    expect(gates[0].method).toBe("PUT");
    gates[0].release();
    await tick();
    expect(gates).toHaveLength(2);
    expect(gates[1].body).toEqual(at(0.5));
    expect(idle).toBe(false);
    gates[1].release();
    await tick();
    expect(idle).toBe(true);
    expect(gates).toHaveLength(2);
    expect(s.busy()).toBe(false);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["pages", 7] });
  });

  it("失敗：hasPendingWrites 為 true、whenIdle reject、通知 error handler；再存一次就清掉", async () => {
    const qc = new QueryClient();
    const s = placementSaverFor(qc);
    const onError = vi.fn();
    s.setErrorHandler(onError);
    s.savePlacement(1, at(0.2));
    expect(hasPendingWrites(qc)).toBe(true);
    gates[0].release(500);
    await tick();
    await expect(s.whenIdle(1)).rejects.toBeTruthy();
    expect(onError).toHaveBeenCalled();
    expect(s.failed(1)).toBe(true);
    expect(hasPendingWrites(qc)).toBe(true);
    s.savePlacement(1, at(0.3));
    gates[1].release();
    await tick();
    expect(hasPendingWrites(qc)).toBe(false);
  });

  it("不同頁互不阻擋", () => {
    const s = placementSaverFor(new QueryClient());
    s.savePlacement(1, at(0.1));
    s.savePlacement(2, at(0.2));
    expect(gates).toHaveLength(2);
  });

  it("頁面刪除：之後的 save 丟掉、404 不報錯；reset 後恢復", async () => {
    const qc = new QueryClient();
    const s = placementSaverFor(qc);
    const onError = vi.fn();
    s.setErrorHandler(onError);
    s.savePlacement(1, at(0.2));
    forgetDeletedPage(qc, 1);
    gates[0].release(404);
    await tick();
    expect(onError).not.toHaveBeenCalled();
    expect(s.failed(1)).toBe(false);
    s.savePlacement(1, at(0.4));
    expect(gates).toHaveLength(1);
    forgetPageId(qc, 1);
    s.savePlacement(1, at(0.4));
    expect(gates).toHaveLength(2);
  });
});
