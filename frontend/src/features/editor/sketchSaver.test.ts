import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sketchSaverFor } from "./sketchSaver";

const tick = () => new Promise((r) => setTimeout(r, 0));
let n = 0;

beforeEach(() => {
  n = 0;
  URL.createObjectURL = vi.fn(() => `blob:${++n}`);
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

describe("sketchSaverFor", () => {
  it("同一個 QueryClient 共用一個 saver", () => {
    const qc = new QueryClient();
    expect(sketchSaverFor(qc)).toBe(sketchSaverFor(qc));
  });

  it("latest／latestUrl：沒存過 undefined、空白 null、blob 有固定的 object URL；寫入失敗也保留", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "壞了" }), { status: 500 })));
    const s = sketchSaverFor(new QueryClient());
    const onError = vi.fn();
    s.setErrorHandler(onError);
    expect(s.latest(1)).toBeUndefined();
    expect(s.latestUrl(1)).toBeUndefined();
    const b = new Blob(["x"]);
    s.save(1, 1, b);
    expect(s.latest(1)).toBe(b);
    expect(s.latestUrl(1)).toBe("blob:1");
    expect(s.latestUrl(1)).toBe("blob:1");
    await tick();
    await tick();
    expect(onError).toHaveBeenCalled();
    expect(s.latest(1)).toBe(b);
    s.save(1, 1, null);
    expect(s.latest(1)).toBeNull();
    expect(s.latestUrl(1)).toBeNull();
    expect(s.latest(2)).toBeUndefined();
  });

  it("新的 blob 換新 URL，舊的延後釋放；forget 清掉", () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
      const s = sketchSaverFor(new QueryClient());
      s.save(1, 1, new Blob(["a"]));
      expect(s.latestUrl(1)).toBe("blob:1");
      s.save(1, 1, new Blob(["b"]));
      expect(s.latestUrl(1)).toBe("blob:2");
      expect(URL.revokeObjectURL).not.toHaveBeenCalled();
      vi.advanceTimersByTime(60_000);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:1");
      s.forget(1);
      expect(s.latest(1)).toBeUndefined();
      vi.advanceTimersByTime(60_000);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:2");
    } finally {
      vi.useRealTimers();
    }
  });

  // ---------- final review fixes ----------

  const jsonRes = (status: number, body: unknown = {}) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

  it("flush 叫已登記的畫布立即存檔；whenIdle 等寫入完成，失敗則 reject 且 failed 持續到重試", async () => {
    let status = 500;
    let release!: () => void;
    let gate = new Promise<void>((r) => (release = r));
    const fetchMock = vi.fn(async () => {
      await gate;
      return jsonRes(status, { detail: "壞了" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const s = sketchSaverFor(new QueryClient());
    s.setErrorHandler(() => {});
    const blob = new Blob(["png"]);
    let dirty = true;
    const flush = vi.fn(() => {
      if (!dirty) return;
      dirty = false;
      s.save(1, 7, blob);
    });
    const off = s.register(1, 7, { flush, dirty: () => dirty });
    expect(s.hasPending()).toBe(true); // 畫布有未存變更
    s.flush(7);
    expect(flush).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const w = s.whenIdle(7);
    release();
    await expect(w).rejects.toThrow("壞了");
    expect(s.failed(7)).toBe(true);
    expect(s.hasPending()).toBe(true); // 失敗也算未存
    // 重試：再送一次最後存的內容
    status = 200;
    gate = Promise.resolve();
    s.retry(7);
    expect(flush).toHaveBeenCalledTimes(2); // 重試先 flush 畫布（這裡沒有新變更）
    expect(s.failed(7)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await expect(s.whenIdle(7)).resolves.toBeUndefined();
    expect(s.hasPending()).toBe(false);
    off();
    s.flush(7); // 已取消登記：不呼叫
    expect(flush).toHaveBeenCalledTimes(2);
  });

  it("subscribe：失敗與重試成功都通知", async () => {
    let status = 500;
    vi.stubGlobal("fetch", vi.fn(async () => jsonRes(status, { detail: "x" })));
    const s = sketchSaverFor(new QueryClient());
    const fn = vi.fn();
    s.subscribe(fn);
    s.save(1, 1, new Blob(["a"]));
    await s.whenIdle(1).catch(() => {});
    expect(s.failed(1)).toBe(true);
    expect(fn).toHaveBeenCalled();
    fn.mockClear();
    status = 200;
    s.retry(1);
    await s.whenIdle(1);
    expect(fn).toHaveBeenCalled();
    expect(s.failed(1)).toBe(false);
  });

  it("whenBookIdle：先 flush 這本書的畫布，再等這本書所有頁的寫入；別本書不等", async () => {
    const gates: Record<string, () => void> = {};
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => new Promise<Response>((r) => (gates[url] = () => r(jsonRes(200))))),
    );
    const s = sketchSaverFor(new QueryClient());
    s.save(2, 20, new Blob(["other book"])); // 別本書：永遠不完成
    let dirty = true;
    s.register(1, 10, {
      flush: () => {
        if (dirty) s.save(1, 10, new Blob(["x"]));
        dirty = false;
      },
      dirty: () => dirty,
    });
    let done = false;
    const w = s.whenBookIdle(1).then(() => (done = true));
    expect(gates["/api/pages/10/sketch"]).toBeDefined();
    await tick();
    expect(done).toBe(false);
    gates["/api/pages/10/sketch"]();
    await w;
    expect(done).toBe(true);
  });

  it("刪除中的頁：卸載 flush 的存檔不送出；刪除成功後忘掉，飛行中寫入的 404 不回報", async () => {
    const gates: (() => void)[] = [];
    const fetchMock = vi.fn(
      () => new Promise<Response>((r) => gates.push(() => r(jsonRes(404, { detail: "找不到頁面" })))),
    );
    vi.stubGlobal("fetch", fetchMock);
    const s = sketchSaverFor(new QueryClient());
    const onError = vi.fn();
    s.setErrorHandler(onError);
    s.save(1, 5, new Blob(["a"])); // DELETE 前就在路上的 PUT
    s.beginDelete(5);
    s.save(1, 5, new Blob(["b"])); // 畫布卸載 flush：先停著
    expect(fetchMock).toHaveBeenCalledTimes(1);
    s.endDelete(5, true);
    expect(s.latest(5)).toBeUndefined();
    gates[0]();
    await s.whenIdle(5);
    expect(onError).not.toHaveBeenCalled();
    expect(s.failed(5)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(s.hasPending()).toBe(false);
  });

  it("刪除失敗：停著的存檔照常送出", async () => {
    const fetchMock = vi.fn(async () => jsonRes(200));
    vi.stubGlobal("fetch", fetchMock);
    const s = sketchSaverFor(new QueryClient());
    s.beginDelete(5);
    const b = new Blob(["b"]);
    s.save(1, 5, b);
    expect(fetchMock).not.toHaveBeenCalled();
    s.endDelete(5, false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(s.latest(5)).toBe(b);
    await s.whenIdle(5);
  });
});
