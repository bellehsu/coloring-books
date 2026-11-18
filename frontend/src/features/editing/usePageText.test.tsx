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
});
