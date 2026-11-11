import { expect, test } from "@playwright/test";

const REVOKED = "連結已失效，請向邀請你的人要新的連結";
const MISSING = "這個瀏覽器還沒開過邀請連結。請用邀請你的人給的連結開啟（換瀏覽器或 App 時要再開一次連結）。";

test("無效、截斷或多出段落的邀請連結都到「連結已失效」頁（不是後端的 JSON）", async ({ page }) => {
  for (const path of ["/i/not-a-real-token", "/i", "/i/", "/i/abc/def"]) {
    await page.goto(path);
    await expect(page, path).toHaveURL(/\/invalid-link$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("連結已失效");
    await expect(page.getByText(REVOKED)).toBeVisible();
  }
});

test("這個瀏覽器的 session 無效（例如換了瀏覽器、資料庫重建）：請再開一次邀請連結，不說連結失效", async ({
  browser,
  baseURL,
}) => {
  const ctx = await browser.newContext({ baseURL });
  try {
    // 壞掉的 cookie 一律 401「請用邀請連結開啟」（不會退回 dev_owner）
    await ctx.addCookies([{ name: "cb_session", value: "stale-session-id", url: baseURL! }]);
    const page = await ctx.newPage();
    await page.goto("/");
    await expect(page).toHaveURL(/\/invalid-link\?reason=missing$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("請用邀請連結開啟");
    await expect(page.getByText(MISSING)).toBeVisible();
    await expect(page.getByText(/連結已失效/)).toHaveCount(0);
  } finally {
    await ctx.close();
  }
});
