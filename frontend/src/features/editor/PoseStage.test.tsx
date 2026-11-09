import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Generation } from "@/api/client";
import { PoseStage } from "./PoseStage";

const pose = { id: 5, image_url: "/i.png", mask_url: "/m.png", width: 832, height: 1216 } as unknown as Generation;

describe("PoseStage", () => {
  it("shows an empty state", () => {
    render(<PoseStage pose={undefined} />);
    expect(screen.getByText("還沒有角色動作")).toBeInTheDocument();
  });
  it("cuts the pose out with the mask", () => {
    render(<PoseStage pose={pose} />);
    const img = screen.getByRole("img", { name: "角色動作（去背預覽）" });
    expect(img).toHaveAttribute("src", "/i.png");
    expect(img.style.maskImage || img.style.getPropertyValue("-webkit-mask-image")).toContain("/m.png");
  });
  it("shows the plain image when there is no mask", () => {
    render(<PoseStage pose={{ ...pose, mask_url: null } as unknown as Generation} />);
    expect(screen.getByRole("img", { name: "角色動作（去背預覽）" }).style.maskImage).toBe("");
  });
});
