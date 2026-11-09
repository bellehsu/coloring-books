import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Generation } from "@/api/client";
import { PoseStage } from "./PoseStage";

const pose = { id: 5, image_url: "/i.png", mask_url: "/m.png", cutout_url: "/c.png", width: 832, height: 1216 } as unknown as Generation;

describe("PoseStage", () => {
  it("shows an empty state", () => {
    render(<PoseStage pose={undefined} />);
    expect(screen.getByText("還沒有角色動作")).toBeInTheDocument();
  });
  it("shows the cutout image on the checkerboard without CSS masks", () => {
    render(<PoseStage pose={pose} />);
    const img = screen.getByRole("img", { name: "角色動作（去背預覽）" });
    expect(img).toHaveAttribute("src", "/c.png");
    expect(img.getAttribute("style") ?? "").not.toContain("mask");
  });
  it("says so when there is no cutout", () => {
    render(<PoseStage pose={{ ...pose, mask_url: null, cutout_url: null } as unknown as Generation} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("這個角色動作沒有去背圖")).toBeInTheDocument();
  });
});
