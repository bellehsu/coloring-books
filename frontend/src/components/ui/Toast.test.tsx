import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "./Toast";

function Btn({ tone }: { tone?: "error" | "success" }) {
  const t = useToast();
  return <button onClick={() => t.show("訊息", tone)}>go</button>;
}

afterEach(() => vi.useRealTimers());

describe("Toast", () => {
  it("一般用 status，錯誤用 alert，5 秒後消失", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Btn />
        <Btn tone="error" />
      </ToastProvider>,
    );
    const [b1, b2] = screen.getAllByRole("button");
    act(() => b1.click());
    expect(screen.getByRole("status")).toHaveTextContent("訊息");
    act(() => b2.click());
    expect(screen.getByRole("alert")).toHaveTextContent("訊息");
    act(() => {
      vi.advanceTimersByTime(5100);
    });
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
