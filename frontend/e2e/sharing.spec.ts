import { expect, test, type Browser, type BrowserContext } from "@playwright/test";
import { composePage, createBook, createPage, ok, setCharacter, uniqueTitle } from "./seed";

/** 同 isolation.spec：假後端的 POST /api/_test/invite 建立邀請，在全新的 context 開 /i/{token}。 */
async function inviteContext(browser: Browser, baseURL: string, name: string): Promise<BrowserContext> {
  const admin = await browser.newContext({ baseURL });
  const { token } = await ok<{ token: string }>(await admin.request.post("/api/_test/invite", { data: { name } }));
  await admin.close();
  const ctx = await browser.newContext({ baseURL, locale: "zh-TW" });
  const page = await ctx.newPage();
  await page.goto(`/i/${token}`);
  await expect(page).toHaveURL(/\/$/);
  await page.close();
  return ctx;
}

test("A 完成 2 頁中的 1 頁並分享；B 看得到那 1 頁、能下載 PDF；A 取消分享後 B 看到「已經不再分享」", async ({
  browser,
  baseURL,
}) => {
  const a = await inviteContext(browser, baseURL!, "e2e-分享者");
  const b = await inviteContext(browser, baseURL!, "e2e-讀者");
  const anon = await browser.newContext({ baseURL });
  try {
    // A 的 cookie 真的生效（不是 dev admin）：和 isolation.spec 同樣的證明
    expect((await a.cookies()).map((c) => c.name)).toContain("cb_session");
    expect((await a.request.get("/api/system/preflight")).status()).toBe(403);

    const title = uniqueTitle("分享的書");
    const book = await createBook(a.request, title);
    await setCharacter(a.request, book.id);
    const p1 = await createPage(a.request, book.id, "陽光公園", "揮手");
    await composePage(a.request, book.id, p1.id, { compose: true });
    await createPage(a.request, book.id, "海邊", "跑步"); // 第 2 頁沒有完成
    // 完成這一頁（等同編輯器的「完成這一頁 ✓」）：先確認角色一致，再 pass
    const done = (await ok<{ id: number; selected_generation_id: number }[]>(await a.request.get(`/api/books/${book.id}/pages`))).find(
      (p) => p.id === p1.id,
    )!;
    await ok(
      await a.request.post(`/api/pages/${p1.id}/character-check`, {
        data: { generation_id: done.selected_generation_id, result: "consistent" },
      }),
    );
    await ok(await a.request.post(`/api/pages/${p1.id}/pass`, { data: {} }));

    // 沒有完成頁時不能分享的規則由後端測試涵蓋；這裡從 UI 開啟開關
    const pageA = await a.newPage();
    await pageA.goto(`/books/${book.id}/editor`);
    await pageA.getByRole("button", { name: /匯出/ }).first().click();
    const sw = pageA.getByRole("switch", { name: "分享給其他使用者" });
    await expect(sw).toBeEnabled();
    await sw.click(); // 受控開關：伺服器回應後才變成開（check() 會立刻驗證狀態）
    await expect(sw).toBeChecked();
    await expect.poll(async () => (await ok<{ shared_at: string | null }>(await a.request.get(`/api/books/${book.id}`))).shared_at).not.toBeNull();

    // A 自己的首頁：我的繪本有「已分享」徽章，大家的繪本不含自己的書
    await pageA.goto("/");
    await expect(pageA.getByRole("link", { name: new RegExp(title) })).toContainText("已分享");

    // B：大家的繪本 → 打開 → 只有 1 頁圖 → PDF
    const pageB = await b.newPage();
    await pageB.goto("/");
    await expect(pageB.getByRole("heading", { name: "大家的繪本" })).toBeVisible();
    const card = pageB.getByRole("link", { name: new RegExp(title) });
    await expect(card).toContainText("作者：e2e-分享者");
    await expect(card).toContainText("1 頁");
    await card.click();
    await expect(pageB).toHaveURL(new RegExp(`/shared/${book.id}$`));
    await expect(pageB.getByRole("heading", { name: title })).toBeVisible();
    await expect(pageB.getByText("作者：e2e-分享者")).toBeVisible();
    const imgs = pageB.getByRole("img", { name: /^第 \d+ 頁$/ });
    await expect(imgs).toHaveCount(1);
    await expect.poll(() => imgs.first().evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBeGreaterThan(0);

    const [download] = await Promise.all([
      pageB.waitForEvent("download"),
      pageB.getByRole("link", { name: /下載 PDF/ }).click(),
    ]);
    const path = await download.path();
    const { readFileSync } = await import("node:fs");
    expect(readFileSync(path).subarray(0, 4).toString()).toBe("%PDF");

    // 390 px 寬：唯讀頁沒有水平捲動
    await pageB.setViewportSize({ width: 390, height: 844 });
    await pageB.reload();
    await expect(imgs).toHaveCount(1);
    const { scrollWidth, innerWidth } = await pageB.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);

    // 唯讀：B 打不開 A 的編輯器
    expect((await b.request.get(`/api/books/${book.id}`)).status()).toBe(404);

    // A 取消分享 → B 重新整理看到「已經不再分享」，不轉址
    await ok(await a.request.put(`/api/books/${book.id}/share`, { data: { shared: false } }));
    await pageB.reload();
    await expect(pageB.getByText("這本繪本已經不再分享")).toBeVisible();
    await expect(pageB).toHaveURL(new RegExp(`/shared/${book.id}$`));
    await expect(pageB.getByRole("link", { name: "回首頁" })).toHaveAttribute("href", "/");
    await pageB.goto("/");
    await expect(pageB.getByText(title)).toHaveCount(0);
  } finally {
    await a.close();
    await b.close();
    await anon.close();
  }
});
