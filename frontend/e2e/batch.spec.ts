import { expect, test, type Page } from "@playwright/test";
import { createBook, createPage, uniqueTitle } from "./seed";

const pageButton = (page: Page, n: number) => page.getByRole("button", { name: new RegExp(`^第 ${n} 頁(?!的更多動作)`) });

test("產生所有背景：有場景描述的頁各排一張背景，沒有的略過", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 產生所有背景"));
  const p1 = await createPage(request, book.id, "in a sunny park");
  await createPage(request, book.id, "on a quiet beach");
  const p3 = await createPage(request, book.id, "");

  await page.goto(`/books/${book.id}/editor?page=${p1.id}`);
  await expect(
    page.getByText("先寫好每一頁的場景描述，再按頁面清單上方的『產生所有背景』，可以減少等待"),
  ).toBeVisible();
  const all = page.getByRole("button", { name: "產生所有背景" });
  await expect(all).toBeEnabled();

  // 先掛上監聽，取消那一步送出的請求也會被算到
  const batchUrl = `/api/books/${book.id}/backgrounds/batch`;
  let posts = 0;
  page.on("request", (r) => {
    if (r.url().endsWith(batchUrl) && r.method() === "POST") posts++;
  });

  // 取消：不送出
  await all.click();
  const dialog = page.getByRole("dialog", { name: "產生所有背景？" });
  await expect(dialog).toContainText("會為 2 頁產生背景（1 頁略過：第 3 頁沒有場景描述）");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();
  expect(posts).toBe(0);

  // 確認：一次 POST，後端排入 2 頁、略過第 3 頁
  const answered = page.waitForResponse((r) => r.url().endsWith(batchUrl) && r.request().method() === "POST");
  await all.click();
  await dialog.getByRole("button", { name: "產生 2 頁背景" }).click();
  const res = await answered;
  expect(res.status()).toBe(202);
  const body = (await res.json()) as { queued: unknown[]; skipped: { page_id: number; reason: string }[] };
  expect(body.queued).toHaveLength(2);
  expect(body.skipped).toEqual([expect.objectContaining({ page_id: p3.id, reason: "沒有場景描述" })]);
  await expect(page.getByText("已排入 2 頁背景、略過 1 頁")).toBeVisible();

  // 第 1、2 頁各一張背景（假後端很快就完成），第 3 頁沒有
  const history = page.getByRole("region", { name: "背景歷史" });
  const done = history.getByRole("button", { name: /^選 seed \d+ 為本頁背景$/ });
  await expect(history.getByRole("listitem")).toHaveCount(1);
  await expect(done).toHaveCount(1);
  await pageButton(page, 2).click();
  await expect(pageButton(page, 2)).toHaveAttribute("aria-current", "true");
  await expect(history.getByRole("listitem")).toHaveCount(1);
  await expect(done).toHaveCount(1);
  await pageButton(page, 3).click();
  await expect(pageButton(page, 3)).toHaveAttribute("aria-current", "true");
  await expect(history.getByText("還沒有背景。寫好場景描述後按「產生背景」。")).toBeVisible();
  await expect(history.getByRole("listitem")).toHaveCount(0);

  // 都有背景或沒有描述了 → 停用並說明；只送出過一次
  await expect(all).toBeDisabled();
  await expect(all).toHaveAttribute("title", "沒有需要產生背景的頁面");
  expect(posts).toBe(1);
});
