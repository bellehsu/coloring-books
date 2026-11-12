import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Book } from "@/api/client";
import { AppBar } from "./AppBar";
import { BookBar } from "./BookBar";

vi.mock("@/api/hooks", () => ({
  useQueue: () => ({ data: { state: "idle", last_error: null, queued: 0 } }),
}));

const book = (o: Partial<Book> = {}) =>
  ({
    id: 7,
    title: "小兔子的一天",
    paper_size: "a4",
    print_mode: "print_shop",
    character_generation_id: 3,
    stats: { pages: 20, passed: 14 },
    ...o,
  }) as Book;

describe("BookBar", () => {
  it("書名、版面說明、主角與故事連到 setup", () => {
    render(<BookBar book={book()} />);
    expect(screen.getByRole("heading", { name: "小兔子的一天" })).toBeInTheDocument();
    expect(screen.getByText(/A4・印刷廠/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "主角與故事" })).toHaveAttribute("href", "/books/7/setup");
  });

  it("沒有舊的四步驟導覽（角色／故事／頁面）", () => {
    render(<BookBar book={book()} />);
    expect(screen.queryByRole("list", { name: "製作步驟" })).not.toBeInTheDocument();
    expect(screen.queryByText(/頁面 14/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^角色/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^故事/ })).not.toBeInTheDocument();
  });

  it("匯出 PDF 呼叫 onExport，繪本設定呼叫 onSettings", async () => {
    const user = userEvent.setup();
    const onExport = vi.fn();
    const onSettings = vi.fn();
    render(<BookBar book={book()} onExport={onExport} onSettings={onSettings} />);
    await user.click(screen.getByRole("button", { name: "匯出 PDF" }));
    expect(onExport).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "繪本設定" }));
    expect(onSettings).toHaveBeenCalled();
  });
});

describe("AppBar", () => {
  it("logo、麵包屑連結與目前位置", () => {
    render(<AppBar crumbs={[{ label: "我的繪本", href: "/" }, { label: "小兔子的一天" }]} />);
    expect(screen.getByRole("link", { name: /繪本工作室/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "我的繪本" })).toHaveAttribute("href", "/");
    expect(screen.getByText("小兔子的一天")).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("GPU 閒置")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "繪本工作室" })).toBeInTheDocument();
  });
});
