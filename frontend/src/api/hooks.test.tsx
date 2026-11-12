import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { placementSaverFor } from "@/features/editor/placementSaver";
import {
  useBatchMaterials,
  useBookOverview,
  OVERVIEW_POLL_MS,
  useCharacterCandidates,
  useCompose,
  useGenerateBackground,
  useGeneratePose,
  useGenerations,
  useQueue,
  useSelectBackground,
  useSelectPose,
  useUploadBackground,
  usePagesFollowJobs,
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

describe("useBookOverview background polling", () => {
  const active = {
    book_id: 1,
    pages: [],
    progress: { queued: 1, running: 0, done: 0 },
    estimate_s: 10,
    deferred_count: 0,
    run_started_at: null,
  };
  afterEach(() => focusManager.setFocused(undefined));

  async function pollsInBackground(opts?: { background?: boolean }) {
    const f = vi.fn().mockImplementation(async () => jsonRes(active));
    vi.stubGlobal("fetch", f);
    const { wrapper } = setup();
    renderHook(() => useBookOverview(1, opts), { wrapper });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    act(() => focusManager.setFocused(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS * 2 + 100); });
    return f.mock.calls.length;
  }

  it("C overview does not poll in background", async () => {
    expect(await pollsInBackground()).toBe(1);
  });

  it("B overview (background: true) keeps polling in a background tab", async () => {
    expect(await pollsInBackground({ background: true })).toBe(3);
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

  it("useGenerateBackground POST /pages/5/backgrounds 不帶風格，invalidate generations 與 queue", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useGenerateBackground(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5 }); });
    expect(f.mock.calls[0][0]).toBe("/api/pages/5/backgrounds");
    expect(f.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({});
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
    expect(keys()).toContain(JSON.stringify(qk.queue()));
  });

  it("useUploadBackground posts multipart to /pages/{id}/backgrounds/upload，invalidate pages 與 generations", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useUploadBackground(1), { wrapper });
    const file = new File(["png"], "forest.png", { type: "image/png" });
    let page: unknown;
    await act(async () => { page = await result.current.mutateAsync({ pageId: 5, file }); });
    expect(page).toEqual({ id: 5, book_id: 1 });
    expect(f.mock.calls[0][0]).toBe("/api/pages/5/backgrounds/upload");
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const sent = (init.body as FormData).get("file") as File;
    expect(sent.name).toBe("forest.png");
    // multipart：不自己設 Content-Type（瀏覽器帶 boundary）
    expect(new Headers(init.headers).get("content-type")).toBeNull();
    // 同 useSelectBackground：上傳等於改選背景，頁面的線稿／QA／書的統計都要重抓
    for (const k of [qk.generationsOf(1), qk.pages(1), qk.qa(5), qk.edits(5), qk.book(1), qk.exportSummary(1)])
      expect(keys()).toContain(JSON.stringify(k));
  });

  it("useCharacterCandidates POST /books/1/character/candidates 只帶描述（後端一次一張，不送 count）", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useCharacterCandidates(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ description: "一隻小兔子" }); });
    expect(f.mock.calls[0][0]).toBe("/api/books/1/character/candidates");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ description: "一隻小兔子" });
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
  });

  it("useBatchMaterials POST /books/1/materials/batch 帶 upload_page_ids", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useBatchMaterials(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ upload_page_ids: [4] }); });
    expect(f.mock.calls[0][0]).toBe("/api/books/1/materials/batch");
    expect(f.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ upload_page_ids: [4] });
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
    expect(keys()).toContain(JSON.stringify(qk.pages(1)));
  });

  it("useGeneratePose POST /pages/5/poses 只帶 action（不帶產生方式）", async () => {
    const { f, wrapper, keys } = run();
    const { result } = renderHook(() => useGeneratePose(1), { wrapper });
    await act(async () => { await result.current.mutateAsync({ pageId: 5, action: "揮手" }); });
    expect(f.mock.calls[0][0]).toBe("/api/pages/5/poses");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ action: "揮手" });
    expect(keys()).toContain(JSON.stringify(qk.generationsOf(1)));
  });

  it("hooks 不再提供已移除端點（切換、候選、/select）", async () => {
    const hooks = await import("./hooks");
    for (const name of ["usePoseModes", "useBackgroundStyles", "usePageCandidates", "useSelectGeneration", "useBatchBackgrounds"])
      expect(hooks).not.toHaveProperty(name);
    expect(hooks.qk).not.toHaveProperty("poseModes");
    expect(hooks.qk).not.toHaveProperty("backgroundStyles");
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

describe("usePagesFollowJobs", () => {
  type G = { id: number; status: string };
  function mount(initial: G[] | undefined) {
    const { client, wrapper } = setup();
    const spy = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const keys = () => spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    const r = renderHook(({ gens }: { gens: G[] | undefined }) => usePagesFollowJobs(1, gens as never), {
      wrapper,
      initialProps: { gens: initial },
    });
    return { keys, rerender: (gens: G[] | undefined) => r.rerender({ gens }) };
  }

  it("排隊／執行中的任務結束（worker 可能自動選用）→ 重抓 pages 與 book", () => {
    const { keys, rerender } = mount([{ id: 1, status: "queued" }, { id: 2, status: "running" }]);
    expect(keys()).toEqual([]);
    rerender([{ id: 1, status: "running" }, { id: 2, status: "running" }]);
    expect(keys()).toEqual([]);
    rerender([{ id: 1, status: "succeeded" }, { id: 2, status: "running" }]);
    expect(keys()).toEqual([JSON.stringify(qk.pages(1)), JSON.stringify(qk.book(1))]);
    rerender([{ id: 1, status: "succeeded" }, { id: 2, status: "failed" }]);
    expect(keys()).toHaveLength(4);
  });

  it("一開始就已完成的、新出現的任務、載入中（undefined）都不觸發", () => {
    const { keys, rerender } = mount(undefined);
    rerender([{ id: 1, status: "succeeded" }]);
    rerender([{ id: 1, status: "succeeded" }, { id: 2, status: "queued" }]);
    rerender(undefined);
    expect(keys()).toEqual([]);
  });
});
