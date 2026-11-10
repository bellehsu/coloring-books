import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PHONE_QUERY, useMediaQuery } from "./useMediaQuery";

function fakeMatchMedia(initial: boolean) {
  const listeners = new Set<() => void>();
  const m = {
    matches: initial,
    addEventListener: vi.fn((_: string, fn: () => void) => listeners.add(fn)),
    removeEventListener: vi.fn((_: string, fn: () => void) => listeners.delete(fn)),
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => m),
  );
  return {
    m,
    set(v: boolean) {
      m.matches = v;
      listeners.forEach((fn) => fn());
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("useMediaQuery", () => {
  it("沒有 matchMedia（jsdom）時是 false", () => {
    vi.stubGlobal("matchMedia", undefined);
    const { result } = renderHook(() => useMediaQuery(PHONE_QUERY));
    expect(result.current).toBe(false);
  });

  it("跟著 media query 變化，卸載時取消監聽", () => {
    const mq = fakeMatchMedia(true);
    const { result, unmount } = renderHook(() => useMediaQuery(PHONE_QUERY));
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 640px)");
    expect(result.current).toBe(true);
    act(() => mq.set(false));
    expect(result.current).toBe(false);
    unmount();
    expect(mq.m.removeEventListener).toHaveBeenCalled();
  });
});
