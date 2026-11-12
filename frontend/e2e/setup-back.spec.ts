import { expect, test } from "@playwright/test";
import { ok, uniqueTitle } from "./seed";

/** D1：在 /books/new 建立書後（網址被 replaceState 換成 /books/{id}/setup），開始做這本書 → 瀏覽器「上一頁」要回到這本書的設定，不是空白新書。 */
test("建立書、開始做這本書，再按上一頁：看到這本書的「主角與故事」，不會再建一本重複的書", async ({ page }) => {
  test.setTimeout(90_000);
  const title = uniqueTitle("e2e 上一頁");
  const countBooks = async () => (await ok<unknown[]>(await page.request.get("/api/books"))).length;

  await page.goto("/books/new");
  const created = page.waitForResponse((r) => r.url().endsWith("/api/books") && r.request().method() === "POST");
  await page.getByLabel("書名", { exact: true }).fill(title);
  await page.getByLabel("主角長什麼樣子？").fill("一隻圓圓的小白兔");
  expect((await created).status()).toBe(201);
  await expect(page).toHaveURL(/\/books\/(\d+)\/setup$/);
  const bookId = Number(/\/books\/(\d+)\/setup/.exec(page.url())![1]);

  await page.getByRole("button", { name: "＋ 產生主角" }).click();
  await page.getByRole("button", { name: "選這隻" }).click();
  await expect(page.getByRole("button", { name: "就是牠 ✓" })).toBeVisible();
  await page.getByRole("button", { name: "套用主題包" }).click();
  await page.getByRole("dialog", { name: "套用主題包" }).getByRole("button", { name: /交通工具/ }).click();
  await expect(page.getByText("讀到 10 段")).toBeVisible();
  await page.getByRole("button", { name: "開始做這本書" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}$`));

  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}/setup$`));
  await expect(page.getByRole("heading", { level: 1, name: "主角與故事" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "做一本新的著色書" })).toHaveCount(0);
  const titleField = page.getByLabel("書名", { exact: true });
  await expect(titleField).toHaveValue(title);

  const before = await countBooks();
  await titleField.fill(`${title} 改`);
  await titleField.blur();
  await page.waitForResponse((r) => r.url().endsWith(`/api/books/${bookId}`) && r.request().method() === "PATCH");
  expect(await countBooks()).toBe(before);
});
