import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { THEME_PACKS } from "@/lib/themePacks";
import { ThemePackDialog } from "./ThemePackDialog";

describe("ThemePackDialog", () => {
  it("lists the pack and reports the chosen one", async () => {
    const onChoose = vi.fn();
    render(<ThemePackDialog open onClose={vi.fn()} onChoose={onChoose} />);
    expect(screen.getByText("交通工具")).toBeInTheDocument();
    expect(screen.getByText("職業")).toBeInTheDocument();
    expect(screen.getByText("衛生習慣")).toBeInTheDocument();
    expect(screen.getByText("環遊世界")).toBeInTheDocument();
    expect(screen.getByText("台灣景點")).toBeInTheDocument();
    expect(screen.getByText(THEME_PACKS[0].description)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /交通工具/ }));
    expect(onChoose).toHaveBeenCalledWith(THEME_PACKS[0]);
  });
});
