import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useGenerations, useQueue, useSelectGeneration, qk } from "./hooks";

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
