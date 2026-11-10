import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { placementSaverFor } from "@/features/editor/placementSaver";
import {
  useBackgroundStyles,
  useBatchBackgrounds,
  useCompose,
  useGenerateBackground,
  useGeneratePose,
  useGenerations,
  usePoseModes,
  useQueue,
  useSelectBackground,
  useSelectGeneration,
  useSelectPose,
  qk,
} from "./hooks";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

const gen = (status: string) => ({ id: 1, status });
function jsonRes(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("usePoseModes", () => {
  it("GET /system/pose-modes，一分鐘內不重抓", async () => {
    const body = { precise: { available: false, missing: ["ComfyUI-GGUF"] }, fast: { available: true, missing: [] } };
    const f = vi.fn().mockImplementation(async () => jsonRes(body));
    vi.stubGlobal("fetch", f);
    const { wrapper } = setup();
    const a = renderHook(() => usePoseModes(), { wrapper });
    await waitFor(() => expect(a.result.current.data).toEqual(body));
    expect(f.mock.calls[0][0]).toBe("/api/system/pose-modes");
    a.unmount();
    renderHook(() => usePoseModes(), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("enabled=false 時不抓", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const { wrapper } = setup();
    renderHook(() => usePoseModes(false), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(f).not.toHaveBeenCalled();
  });
});

describe("useBackgroundStyles", () => {
  it("GET /system/background-styles，一分鐘內不重抓；enabled=false 時不抓", async () => {
    const body = { simple: { available: false, missing: ["ComfyUI-GGUF"] }, detailed: { available: true, missing: [] } };
    const f = vi.fn().mockImplementation(async () => jsonRes(body));
    vi.stubGlobal("fetch", f);
    const { wrapper } = setup();
    renderHook(() => useBackgroundStyles(false), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(f).not.toHaveBeenCalled();
    const a = renderHook(() => useBackgroundStyles(), { wrapper });
    await waitFor(() => expect(a.result.current.data).toEqual(body));
    expect(f.mock.calls[0][0]).toBe("/api/system/background-styles");
    a.unmount();
    renderHook(() => useBackgroundStyles(), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(f).toHaveBeenCalledTimes(1);
  });
});

describe("polling", () => {
  it("useGenerations refetches every 2s while queued, then stops", async () => {
    const responses = [[gen("queued")], [gen("running")], [gen("succeeded")]];
    let n = 0;
    const f = vi.fn().mockImplementation(async () => jsonRes(responses[Math.min(n++, 2)]));
    vi.stubGlobal("fetch", f);
    const { wrapper } = setup();
    renderHook(() => useGenerations(1, { kind: "page", pageId: 5 }), { wrapper });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    expect(f.mock.calls[0][0]).toBe("/api/books/1/generations?kind=page&page_id=5");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(f).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(f).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(f).toHaveBeenCalledTimes(3);
  });

  it("useQueue polls every 5s", async () => {
    const f = vi.fn().mockImplementation(async () => jsonRes({ state: "idle", queued: 0 }));
    vi.stubGlobal("fetch", f);
    const { wrapper } = setup();
    renderHook(() => useQueue(), { wrapper });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
    expect(f).toHaveBeenCalledTimes(2);
  });
});

describe("mutations", () => {
  it("useSelectGeneration invalidates pages and qa", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => jsonRes({ id: 5, book_id: 1 })));
    const { client, wrapper } = setup();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useSelectGeneration(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5, generationId: 9 }); });
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.pages(1)));
    expect(keys).toContain(JSON.stringify(qk.qa(5)));
    expect(keys).toContain(JSON.stringify(qk.edits(5)));
    expect(keys).toContain(JSON.stringify(qk.book(1)));
  });
});

describe("compose-flow hooks", () => {
  function run() {
    const f = vi.fn().mockImplementation(async () => jsonRes({ id: 5, book_id: 1 }));
    vi.stubGlobal("fetch", f);
    const { client, wrapper } = setup();
    const spy = vi.spyOn(client, "invalidateQueries");
    const keys = () => spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    return { f, wrapper, keys };
  }

  it("useGenerateBackground POST /pages/5/backgrounds 帶 style，invalidate generations 與 queue", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useGenerateBackground(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5, style: "simple" }); });
    expect(f.mock.calls[0][0]).toBe("/api/pages/5/backgrounds");
    expect(f.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ style: "simple" });
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
    expect(keys()).toContain(JSON.stringify(qk.queue()));
  });

  it("useBatchBackgrounds POST /books/1/backgrounds/batch 帶 style", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useBatchBackgrounds(1), { wrapper });
    await act(async () => { await result.current.mutateAsync("detailed"); });
    expect(f.mock.calls[0][0]).toBe("/api/books/1/backgrounds/batch");
    expect(f.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ style: "detailed" });
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
  });

  it("useGeneratePose POST /pages/5/poses 帶 action 與 mode", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useGeneratePose(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5, action: "揮手", mode: "fast" }); });
    expect(f.mock.calls[0][0]).toBe("/api/pages/5/poses");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ action: "揮手", mode: "fast" });
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
  });

  it("useGeneratePose: 503 invalidates pose-modes; other errors do not", async () => {
    for (const [status, expected] of [[503, true], [409, false]] as const) {
      const f = vi.fn().mockImplementation(
        async () =>
          new Response(JSON.stringify({ detail: "精準模式缺少：x" }), {
            status,
            headers: { "content-type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", f);
      const { client, wrapper } = setup();
      const spy = vi.spyOn(client, "invalidateQueries");
      const { result } = renderHook(() => useGeneratePose(1), { wrapper });
      await act(async () => {
        await result.current.mutateAsync({ pageId: 5, action: "揮手", mode: "precise" }).catch(() => {});
      });
      const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
      expect(keys.includes(JSON.stringify(qk.poseModes()))).toBe(expected);
    }
  });

  it.each([
    ["useSelectBackground", useSelectBackground, "background"],
    ["useSelectPose", useSelectPose, "pose"],
  ] as const)("%s POST /pages/5/%s 帶 generation_id，invalidate 頁面", async (_n, hook, path) => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => hook(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5, generationId: 9 }); });
    expect(f.mock.calls[0][0]).toBe(`/api/pages/5/${path}`);
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ generation_id: 9 });
    for (const k of [qk.generationsOf(1), qk.pages(1), qk.qa(5), qk.edits(5), qk.book(1), qk.exportSummary(1)])
      expect(keys()).toContain(JSON.stringify(k));
  });

  it("useCompose POST /pages/5/compose 無 body，invalidate pages、qa、edits、book、exportSummary", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useCompose(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5 }); });
    expect(f.mock.calls[0][0]).toBe("/api/pages/5/compose");
    expect(f.mock.calls[0][1].body).toBeUndefined();
    for (const k of [qk.generationsOf(1), qk.pages(1), qk.qa(5), qk.edits(5), qk.book(1), qk.exportSummary(1)])
      expect(keys()).toContain(JSON.stringify(k));
  });

  it("useCompose 等位置 PUT 送完才 POST compose", async () => {
    const order: string[] = [];
    let release: () => void = () => {};
    const f = vi.fn().mockImplementation((url: string) => {
      order.push(url);
      if (url.endsWith("/placement"))
        return new Promise<Response>((r) => (release = () => r(jsonRes({ id: 5, book_id: 1 }))));
      return Promise.resolve(jsonRes({ id: 5, book_id: 1 }));
    });
    vi.stubGlobal("fetch", f);
    const { client, wrapper } = setup();
    placementSaverFor(client).savePlacement(5, { cx: 0.3, cy: 0.6, height: 0.5, flip: false });
    const { result } = renderHook(() => useCompose(1), { wrapper });
    let p: Promise<unknown> = Promise.resolve();
    act(() => { p = result.current.mutateAsync({ pageId: 5 }); });
    await act(async () => { await Promise.resolve(); });
    expect(order).toEqual(["/api/pages/5/placement"]);
    await act(async () => { release(); await p; });
    expect(order).toEqual(["/api/pages/5/placement", "/api/pages/5/compose"]);
  });

  it("useCompose：最後一次位置寫入失敗 → 拒絕且不送 compose", async () => {
    const f = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ detail: "壞了" }), { status: 500, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", f);
    const { client, wrapper } = setup();
    placementSaverFor(client).savePlacement(5, { cx: 0.3, cy: 0.6, height: 0.5, flip: false });
    const { result } = renderHook(() => useCompose(1), { wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync({ pageId: 5 })).rejects.toThrow("位置尚未儲存，請重試");
    });
    expect(f.mock.calls.map((c) => c[0])).toEqual(["/api/pages/5/placement"]);
  });
});
