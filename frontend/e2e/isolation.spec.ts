import { expect, test, type Browser, type BrowserContext } from "@playwright/test";
import { createBook, createPage, ok, setCharacter, uniqueTitle, type Generation } from "./seed";

/** 假後端專用的 POST /api/_test/invite 建立邀請；在全新的瀏覽器 context 開 /i/{token}（設定 cb_session cookie）。 */
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

test("邀請 A 的書，邀請 B 看不到、也打不開；圖片走 302 → /api/files", async ({ browser, baseURL }) => {
  const a = await inviteContext(browser, baseURL!, uniqueTitle("e2e-A"));
  const b = await inviteContext(browser, baseURL!, uniqueTitle("e2e-B"));
  // 沒有 cookie 的 context：假後端以 --dev-owner admin 啟動，所以它是 admin
  const anon = await browser.newContext({ baseURL });
  try {
    const title = uniqueTitle("A 的書");
    const book = await createBook(a.request, title);
    await createPage(a.request, book.id);
    await setCharacter(a.request, book.id);

    // A 的 cookie 真的生效：不是 cookie 沒設好、被當成沒有 cookie 的 dev admin（那樣下面的斷言也都會過）
    expect((await a.cookies()).map((c) => c.name)).toContain("cb_session");
    expect((await a.request.get("/api/system/preflight")).status()).toBe(403); // 只有 admin 可用
    const adminBook = await createBook(anon.request, uniqueTitle("admin 的書"));
    expect((await ok<{ id: number }[]>(await a.request.get("/api/books"))).map((x) => x.id)).toEqual([book.id]);
    expect((await a.request.get(`/api/books/${adminBook.id}`)).status()).toBe(404);

    // A 看得到自己的書，角色圖載入成功：API 先 302 到簽章網址 /api/files/...，最後是 image/png
    const pageA = await a.newPage();
    await pageA.goto("/");
    await expect(pageA.getByText(title)).toBeVisible();
    const [gen] = await ok<Generation[]>(await a.request.get(`/api/books/${book.id}/generations?kind=character`));
    const redirect = await a.request.get(`/api/generations/${gen.id}/image`, { maxRedirects: 0 });
    expect(redirect.status()).toBe(302);
    expect(redirect.headers()["location"]).toMatch(/\/api\/files\/.+[?&]sig=/);
    const img = await a.request.get(`/api/generations/${gen.id}/image`);
    expect(img.ok()).toBeTruthy();
    expect(img.headers()["content-type"]).toContain("image/png");

    // B 的書單是空的，看不到 A 的書
    const pageB = await b.newPage();
    await pageB.goto("/");
    await expect(pageB.getByRole("button", { name: "建立新繪本" }).first()).toBeVisible();
    await expect(pageB.getByText(title)).toHaveCount(0);
    expect(await ok<unknown[]>(await b.request.get("/api/books"))).toEqual([]);

    // B 打開 A 的編輯器網址：404（別人的資源），畫面顯示錯誤、不顯示書
    expect((await b.request.get(`/api/books/${book.id}`)).status()).toBe(404);
    expect((await b.request.get(`/api/generations/${gen.id}/image`, { maxRedirects: 0 })).status()).toBe(404);
    await pageB.goto(`/books/${book.id}/editor`);
    await expect(pageB.getByRole("alert")).toBeVisible();
    await expect(pageB.getByText(title)).toHaveCount(0);
  } finally {
    await a.close();
    await b.close();
    await anon.close();
  }
});
