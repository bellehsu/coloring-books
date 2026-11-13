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

test("確認視窗開著時，別張卡的 ⋯ 被背景擋住，不能點", async ({ page, request }) => {
  const a = `甲書 ${Date.now()}`;
  const b = `乙書 ${Date.now()}`;
  await createBook(request, a);
  await createBook(request, b);
  await page.goto("/");
  await page.getByRole("button", { name: `「${a}」的選單` }).click();
  await page.getByRole("menuitem", { name: "刪除這本書" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  // 點別張卡的 ⋯ 位置：實際點到的是背景（Playwright 會因被擋住而逾時）
  await expect(page.getByRole("button", { name: `「${b}」的選單` }).click({ timeout: 1500 })).rejects.toThrow();
});
