import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_PLACEMENT, type Page } from "@/api/client";
import { qk } from "@/api/hooks";
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
            release: (status = 200) =>
              r(
                res(
                  status,
                  status === 200
                    ? { id: Number(url.split("/")[3]), book_id: 7, placement: init.body ? JSON.parse(init.body as string) : null }
                    : { detail: "壞了" },
                ),
              ),
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
    expect(s.hasPending()).toBe(true);
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
    expect(s.hasPending()).toBe(false);
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
    expect(s.isGone(1)).toBe(false);
    forgetDeletedPage(qc, 1);
    expect(s.isGone(1)).toBe(true);
    gates[0].release(404);
    await tick();
    expect(onError).not.toHaveBeenCalled();
    expect(s.failed(1)).toBe(false);
    s.savePlacement(1, at(0.4));
    expect(gates).toHaveLength(1);
    forgetPageId(qc, 1);
    expect(s.isGone(1)).toBe(false);
    s.savePlacement(1, at(0.4));
    expect(gates).toHaveLength(2);
  });

  it("retry 重送最後的位置；沒存過不動作", async () => {
    const s = placementSaverFor(new QueryClient());
    s.retry(9);
    expect(gates).toHaveLength(0);
    s.savePlacement(1, at(0.2));
    gates[0].release(500);
    await tick();
    expect(s.failed(1)).toBe(true);
    s.retry(1);
    expect(gates).toHaveLength(2);
    expect(gates[1].body).toEqual(at(0.2));
    gates[1].release();
    await tick();
    expect(s.failed(1)).toBe(false);
  });

  it("latest 只在這頁送出中或失敗時有值；閒置（存好了）回 undefined，畫面改用伺服器的 page.placement（C 改過也看得到）", async () => {
    const qc = new QueryClient();
    const s = placementSaverFor(qc);
    expect(s.latest(1)).toBeUndefined();
    s.savePlacement(1, at(0.2));
    expect(s.hasPending(1)).toBe(true);
    expect(s.hasPending(2)).toBe(false);
    expect(s.latest(1)).toEqual(at(0.2));
    gates[0].release();
    await tick();
    expect(s.hasPending(1)).toBe(false);
    expect(s.latest(1)).toBeUndefined();
    // 失敗：留著畫面上的位置（重試用）
    s.savePlacement(1, at(0.3));
    gates[1].release(500);
    await tick();
    expect(s.failed(1)).toBe(true);
    expect(s.latest(1)).toEqual(at(0.3));
  });

  it("存好時把回傳的頁寫進頁面快取（latest 清掉時畫面不會先跳回舊位置）", async () => {
    const qc = new QueryClient();
    const old = { id: 1, book_id: 7, placement: at(0.1) } as unknown as Page;
    qc.setQueryData<Page[]>(qk.pages(7), [old, { id: 2, book_id: 7 } as unknown as Page]);
    const s = placementSaverFor(qc);
    s.savePlacement(1, at(0.6));
    const seen: unknown[] = [];
    s.subscribe(() => seen.push(qc.getQueryData<Page[]>(qk.pages(7))?.[0].placement));
    gates[0].release();
    await tick();
    expect(s.latest(1)).toBeUndefined();
    expect(seen.at(-1)).toEqual(at(0.6));
  });
});
