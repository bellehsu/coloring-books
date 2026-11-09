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
});
