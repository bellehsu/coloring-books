import { expect, test } from "@playwright/test";
import { createBook } from "./seed";

test("書架：⋯ → 刪除這本書 → 確認後卡片消失，重新整理也沒有", async ({ page, request }) => {
  const title = `要刪掉的書 ${Date.now()}`;
  const book = await createBook(request, title);
  await page.goto("/");
  await page.getByRole("button", { name: `「${title}」的選單` }).click();
  await page.getByRole("menuitem", { name: "刪除這本書" }).click();
  await expect(page.getByRole("dialog", { name: `確定要刪除「${title}」嗎？` })).toBeVisible();
  await page.getByRole("button", { name: "刪除", exact: true }).click();
  await expect(page.getByRole("button", { name: `「${title}」的選單` })).toHaveCount(0);
  await expect(page.getByText(`已刪除「${title}」`)).toBeVisible();
  expect((await request.get(`/api/books/${book.id}`)).status()).toBe(404);
  await page.reload();
  await expect(page.getByRole("button", { name: `「${title}」的選單` })).toHaveCount(0);
});
