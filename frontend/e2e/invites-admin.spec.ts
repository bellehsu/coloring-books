import { expect, test } from "@playwright/test";
import { ok, uniqueTitle } from "./seed";

/** 管理者在 /admin/invites 產生連結 → 複製 → 在全新的瀏覽器 context 開啟，以新的 owner 登入；之後列表與重新整理都還看得到連結。
 *  假後端以 --dev-owner admin 啟動：沒有 cookie 的 context 就是 admin；有連結 cookie 的 context 是一般使用者。 */
test("管理者產生邀請連結、複製、新的瀏覽器用它登入；連結重新整理後還在", async ({ browser, baseURL }) => {
  // 假後端是 dev_owner 模式：沒有 cookie 的 admin 不能用邀請管理（404），所以管理者要用「管理者邀請連結」登入
  const boot = await browser.newContext({ baseURL });
  const { token: adminToken } = await ok<{ token: string }>(
    await boot.request.post("/api/_test/invite", { data: { name: uniqueTitle("e2e-管理者"), is_admin: true } }),
  );
  await boot.close();
  const admin = await browser.newContext({
    baseURL,
    locale: "zh-TW",
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const fresh = await browser.newContext({ baseURL, locale: "zh-TW" });
  try {
    const name = uniqueTitle("小美");
    const page = await admin.newPage();

    // 沒有 cookie（dev_owner 後備）的 context：首頁沒有「邀請管理」，API 是 404
    const anon = await browser.newContext({ baseURL });
    try {
      const anonPage = await anon.newPage();
      await anonPage.goto("/");
      await expect(anonPage.getByRole("link", { name: "建立新繪本" }).first()).toBeVisible();
      await expect(anonPage.getByRole("link", { name: "邀請管理" })).toHaveCount(0);
      expect((await anon.request.get("/api/admin/invites")).status()).toBe(404);
    } finally {
      await anon.close();
    }

    // 管理者邀請連結登入後，首頁有「邀請管理」連結（只有 cookie 的管理者看得到）
    await page.goto(`/i/${adminToken}`);
    await expect(page).toHaveURL(/\/$/);
    await page.getByRole("link", { name: "邀請管理" }).click();
    await expect(page).toHaveURL(/\/admin\/invites$/);

    await page.getByLabel("名字").fill(name);
    await page.getByRole("checkbox", { name: "管理者" }).check();
    await expect(page.getByLabel("每日上限")).toHaveCount(0); // 管理者不設上限
    await page.getByRole("checkbox", { name: "管理者" }).uncheck();
    await page.getByLabel("每日上限").fill("30");
    await page.getByRole("button", { name: "產生連結" }).click();

    // 新的一列在最上面；連結前綴是瀏覽器開的網址（經過 Next 的 proxy 也是 3010，不是後端的 8010）
    const row = page.getByRole("row").nth(1);
    await expect(row).toContainText(name);
    await expect(row).toContainText("使用者");
    await expect(row).toContainText("30");
    await expect(row).toContainText("使用中");
    await expect(row.locator("code")).toHaveText(new RegExp(`^${baseURL!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/i/[\\w-]+$`));

    await row.getByRole("button", { name: `複製 ${name} 的連結` }).click();
    await expect(row.getByText("已複製")).toBeVisible();
    const link = await page.evaluate(() => navigator.clipboard.readText());
    expect(link).toBe(await row.locator("code").innerText());

    // 另一個瀏覽器用這個連結登入：回到首頁，沒有書，也沒有「邀請管理」，打開管理頁是 404
    const other = await fresh.newPage();
    await other.goto(link);
    await expect(other).toHaveURL(/\/$/);
    await expect(other.getByText("還沒有繪本，建立第一本開始吧。")).toBeVisible();
    await expect(other.getByRole("link", { name: "邀請管理" })).toHaveCount(0);
    await other.goto("/admin/invites"); // 頁面本身是 client 渲染：HTTP 200，內容是 Next 的 404 頁
    await expect(other.getByText("This page could not be found.")).toBeVisible();
    await expect(other.getByRole("table")).toHaveCount(0);
    expect((await fresh.request.get("/api/admin/invites")).status()).toBe(404);

    // 回到管理者：列表有這個人（書 0），重新整理後連結仍在
    await page.reload();
    const again = page.getByRole("row").filter({ hasText: name });
    await expect(again).toHaveCount(1);
    await expect(again.getByRole("cell", { name: "0", exact: true }).first()).toBeVisible();
    await expect(again.locator("code")).toHaveText(link);

    // 390 px：卡片版面，沒有橫向捲動
    await page.setViewportSize({ width: 390, height: 800 });
    await expect(again.locator("code")).toBeVisible();
    const { scrollWidth, innerWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  } finally {
    await admin.close();
    await fresh.close();
  }
});
