import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { Book, Generation, Page } from "@/api/client";
import { BackgroundStage } from "./BackgroundStage";

vi.mock("./SketchCanvas", () => ({
  SketchCanvas: () => <div data-testid="sketch" />,
}));

const layout = {
  generation_size: [832, 1216],
  content_px: [2480, 3508],
  trim_box_px: null,
  safe_box_px: null,
} as unknown as Book["layout"];
const page = { id: 1, sketch_url: null } as unknown as Page;
const bg = { id: 9, image_url: "/api/generations/9/image", width: 1216, height: 832 } as unknown as Generation;

function view(background?: Generation) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <BackgroundStage bookId={1} page={page} layout={layout} background={background} />
    </QueryClientProvider>,
  );
}

describe("BackgroundStage", () => {
  it("without a background shows the sketch board and the hint", () => {
    view();
    expect(screen.getByTestId("sketch")).toBeInTheDocument();
    expect(screen.getByText("可以只寫場景描述，也可以先畫構圖")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "背景圖" })).toBeNull();
  });

  it("with a background shows the image, and the toggle switches to the sketch board", async () => {
    view(bg);
    expect(screen.getByRole("img", { name: "背景圖" })).toHaveAttribute("src", bg.image_url);
    expect(screen.queryByTestId("sketch")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "畫構圖" }));
    expect(screen.getByTestId("sketch")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "背景圖" }));
    expect(screen.getByRole("img", { name: "背景圖" })).toBeInTheDocument();
  });
});
