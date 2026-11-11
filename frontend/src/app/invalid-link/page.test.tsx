import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import InvalidLinkPage, { generateMetadata } from "@/app/invalid-link/page";

async function renderPage(reason?: string | string[]) {
  const searchParams = Promise.resolve(reason === undefined ? {} : { reason });
  return render(await InvalidLinkPage({ searchParams }));
}

describe("/invalid-link", () => {
  it("撤銷或無效的連結（預設，含 /i/{壞 token} 轉來的）：說明連結已失效、要向邀請人要新連結", async () => {
    for (const reason of [undefined, "revoked", "whatever"]) {
      const { unmount } = await renderPage(reason);
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("連結已失效");
      expect(screen.getByText("連結已失效，請向邀請你的人要新的連結")).toBeInTheDocument();
      unmount();
    }
  });

  it("reason=missing：這個瀏覽器還沒開過邀請連結，不說連結失效", async () => {
    await renderPage("missing");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("請用邀請連結開啟");
    expect(
      screen.getByText(
        "這個瀏覽器還沒開過邀請連結。請用邀請你的人給的連結開啟（換瀏覽器或 App 時要再開一次連結）。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/連結已失效/)).toBeNull();
  });

  it("頁面標題跟著原因", async () => {
    expect((await generateMetadata({ searchParams: Promise.resolve({ reason: "missing" }) })).title).toBe(
      "請用邀請連結開啟",
    );
    expect((await generateMetadata({ searchParams: Promise.resolve({}) })).title).toBe("連結已失效");
  });

  it("不呼叫任何 API（沒有登入也要能顯示）", async () => {
    const { container } = await renderPage("missing");
    expect(container.querySelector("a[href^='/api']")).toBeNull();
  });
});
