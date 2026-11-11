import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import InvalidLinkPage from "@/app/invalid-link/page";

describe("/invalid-link", () => {
  it("說明連結已失效、要向邀請人要新連結", () => {
    render(<InvalidLinkPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("連結已失效");
    expect(screen.getByText("連結已失效，請向邀請你的人要新的連結")).toBeInTheDocument();
  });

  it("不呼叫任何 API（沒有登入也要能顯示）", () => {
    const { container } = render(<InvalidLinkPage />);
    expect(container.querySelector("a[href^='/api']")).toBeNull();
  });
});
