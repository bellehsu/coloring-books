import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { usePageText } from "./usePageText";

describe("usePageText", () => {
  it("存好、伺服器追上之後，別處再改的文字會顯示出來", async () => {
    const send = vi.fn(async () => {});
    const { result, rerender } = renderHook(({ server }) => usePageText(server, send), {
      initialProps: { server: "原本" },
    });
    act(() => result.current.setValue("我改的"));
    await act(() => result.current.save());
    expect(send).toHaveBeenCalledWith("我改的");
    rerender({ server: "我改的" }); // 重抓：伺服器追上了
    expect(result.current.value).toBe("我改的");
    rerender({ server: "另一個分頁改的" }); // 之後別處又改了
    expect(result.current.value).toBe("另一個分頁改的");
  });

  it("打字中（還沒存）伺服器的值變了：仍顯示正在打的字", () => {
    const send = vi.fn(async () => {});
    const { result, rerender } = renderHook(({ server }) => usePageText(server, send), {
      initialProps: { server: "原本" },
    });
    act(() => result.current.setValue("打到一半"));
    rerender({ server: "別處改的" });
    expect(result.current.value).toBe("打到一半");
  });

  it("送出中改回原本的字：最後的字（原本的字）也要送出", async () => {
    const pending: (() => void)[] = [];
    const send = vi.fn((v: string) => new Promise<void>((r) => pending.push(r)).then(() => void v));
    const { result } = renderHook(({ server }) => usePageText(server, send), { initialProps: { server: "原本" } });
    act(() => result.current.setValue("X"));
    let first!: Promise<unknown>;
    act(() => {
      first = result.current.save();
    });
    act(() => result.current.setValue("原本")); // X 還在送，又改回原本的字
    let second!: Promise<unknown>;
    act(() => {
      second = result.current.save();
    });
    await act(async () => {
      await vi.waitFor(() => expect(pending).toHaveLength(1));
      pending.shift()!();
      await first;
    });
    await act(async () => {
      await vi.waitFor(() => expect(pending.length).toBeLessThanOrEqual(1));
      pending.shift()?.();
      await second;
    });
    expect(send.mock.calls.map((c) => c[0])).toEqual(["X", "原本"]);
  });

  it("跟著伺服器變了之後，再打回之前存過的字：要重新送出", async () => {
    const send = vi.fn(async (v: string) => void v);
    const { result, rerender } = renderHook(({ server }) => usePageText(server, send), {
      initialProps: { server: "原本" },
    });
    act(() => result.current.setValue("A"));
    await act(() => result.current.save());
    rerender({ server: "A" });
    rerender({ server: "B" }); // 別處改成 B
    act(() => result.current.setValue("A"));
    await act(() => result.current.save());
    expect(send.mock.calls.map((c) => c[0])).toEqual(["A", "A"]);
  });
});
