import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "./Toast";
import { alertToasts, politeToasts } from "./toastTesting";

function Btn({ tone }: { tone?: "error" | "success" }) {
  const t = useToast();
  return <button onClick={() => t.show("訊息", tone)}>go</button>;
}

afterEach(() => vi.useRealTimers());

describe("Toast", () => {
  it("常駐的 live region 一開始就在（空的），訊息加進去才會被唸；一般 polite、錯誤 assertive，5 秒後消失", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Btn />
        <Btn tone="error" />
      </ToastProvider>,
    );
    const polite = politeToasts();
    const assertive = alertToasts();
    expect(polite).toHaveAttribute("aria-live", "polite");
    expect(assertive).toHaveAttribute("aria-live", "assertive");
    expect(polite).toBeEmptyDOMElement();
    expect(assertive).toBeEmptyDOMElement();
    // 訊息本身不是另一個 live region（不會被插入成已經填好字的 role=status／alert）
    expect(screen.queryByRole("status")).toBeNull();

    const [b1, b2] = screen.getAllByRole("button");
    act(() => b1.click());
    expect(politeToasts()).toBe(polite); // 同一個節點，不是重新建立
    expect(polite).toHaveTextContent("訊息");
    expect(assertive).toBeEmptyDOMElement();
    act(() => b2.click());
    expect(alertToasts()).toBe(assertive);
    expect(assertive).toHaveTextContent("訊息");
    expect(screen.queryByRole("alert")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(5100);
    });
    expect(polite).toBeEmptyDOMElement();
    expect(assertive).toBeEmptyDOMElement();
  });
});
