import { render, screen, within } from "@testing-library/react";
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
  it("書名、版面說明、頁面 passed / pages、角色打勾連到角色工作室", () => {
    render(<BookBar book={book()} current="pages" />);
    expect(screen.getByRole("heading", { name: "小兔子的一天" })).toBeInTheDocument();
    expect(screen.getByText(/A4・印刷廠/)).toBeInTheDocument();
    expect(screen.getByText("頁面 14 / 20")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /角色/ })).toHaveAttribute("href", "/books/7/setup");
    expect(screen.getByText("頁面 14 / 20").closest("[aria-current]")).toHaveAttribute("aria-current", "step");
  });

  it("角色未設定：頁面步驟不可點且有說明", () => {
    render(<BookBar book={book({ character_generation_id: null })} current="character" />);
    const pages = screen.getByRole("button", { name: /頁面/ });
    expect(pages).toHaveAttribute("aria-disabled", "true");
    expect(pages).toHaveAccessibleDescription("請先設定角色");
    expect(screen.getByText("請先設定角色")).toBeInTheDocument();
  });

  it("BookBar shows four steps with 故事 disabled before a character", () => {
    render(<BookBar book={book({ character_generation_id: null })} current="character" />);
    const steps = within(screen.getByRole("list", { name: "製作步驟" })).getAllByRole("listitem").filter((li) => li.textContent);
    expect(steps.map((li) => li.textContent)).toEqual(["1角色", "2故事", "3頁面請先設定角色", "4匯出"]);
    const story = screen.getByRole("button", { name: /故事/ });
    expect(story).toHaveAttribute("aria-disabled", "true");
    expect(story).toHaveAccessibleDescription("請先設定角色");
    expect(screen.queryByRole("link", { name: /故事/ })).not.toBeInTheDocument();
  });

  it("角色已設定：故事連到故事頁，目前在故事頁時是 current step", () => {
    const { rerender } = render(<BookBar book={book()} current="pages" />);
    expect(screen.getByRole("link", { name: /故事/ })).toHaveAttribute("href", "/books/7/setup");
    rerender(<BookBar book={book()} current="story" />);
    expect(screen.getByText("故事").closest("[aria-current]")).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("link", { name: /頁面 14 \/ 20/ })).toHaveAttribute("href", "/books/7/editor");
  });

  it("角色已設定時頁面連到編輯器；匯出呼叫 onExport / onSettings", async () => {
    const user = userEvent.setup();
    const onExport = vi.fn();
    const onSettings = vi.fn();
    render(<BookBar book={book()} current="character" onExport={onExport} onSettings={onSettings} />);
    expect(screen.getByRole("link", { name: /頁面/ })).toHaveAttribute("href", "/books/7/editor");
    await user.click(screen.getByRole("button", { name: /匯出$/ }));
    await user.click(screen.getByRole("button", { name: "匯出 PDF" }));
    expect(onExport).toHaveBeenCalledTimes(2);
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
