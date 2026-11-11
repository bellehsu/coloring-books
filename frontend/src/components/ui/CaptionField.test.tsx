import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CaptionField, captionError } from "./CaptionField";

function setup(value: string) {
  const onChange = vi.fn();
  const r = render(<CaptionField kind="zh" value={value} onChange={onChange} onBlur={vi.fn()} />);
  const rerender = (v: string) => r.rerender(<CaptionField kind="zh" value={v} onChange={onChange} onBlur={vi.fn()} />);
  return { input: screen.getByRole("textbox") as HTMLInputElement, rerender, onChange };
}
const live = () => document.querySelector("[aria-live]") as HTMLElement;

describe("captionError / 計數用正規化後的字串", () => {
  it("換行與零寬字元先正規化再數", () => {
    expect(captionError("zh", "一二三四五六七八九十​\n​\n十一十二")).not.toBeNull(); // 10 + 空格 + 4 = 15，超過
    expect(captionError("zh", `${"字".repeat(12)}​\n`)).toBeNull();
    expect(captionError("zh", "一二三四五六七八九十​十一")).toBeNull();
  });

  it("計數器顯示正規化後的剩餘字數", () => {
    setup("消\n防");
    expect(screen.getByText("還可 9 字")).toBeInTheDocument();
  });
});

describe("無障礙：訊息在一直存在的 aria-live=polite 區域", () => {
  it("區域一開始就在、錯誤出現前後是同一個節點，且沒有 role=alert", () => {
    const { rerender } = setup("消防車");
    const region = live();
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveTextContent("");
    rerender("字".repeat(13));
    expect(live()).toBe(region);
    expect(region).toHaveTextContent("中文字最多 12 個字");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("輸入法組字中不顯示超過上限的錯誤", () => {
  it("compositionstart 到 compositionend 之間不顯示，結束後才檢查", () => {
    const { input, rerender } = setup("字".repeat(11));
    fireEvent.compositionStart(input);
    rerender("字".repeat(11) + "ㄏㄢˋㄗ");
    expect(screen.queryByText("中文字最多 12 個字")).toBeNull();
    expect(input).toHaveAttribute("aria-invalid", "false");
    rerender("字".repeat(14));
    fireEvent.compositionEnd(input);
    expect(screen.getByText("中文字最多 12 個字")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("組字結束且沒超過就不出現錯誤", () => {
    const { input, rerender } = setup("字".repeat(10));
    fireEvent.compositionStart(input);
    rerender("字".repeat(11));
    fireEvent.compositionEnd(input);
    expect(live()).toHaveTextContent("");
  });
});
