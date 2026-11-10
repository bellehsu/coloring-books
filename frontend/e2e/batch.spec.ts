import { expect, test, type Page } from "@playwright/test";
import { createBook, createPage, uniqueTitle, uploadSketch } from "./seed";

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
  await expect(dialog).toContainText("會為 2 頁產生背景（簡潔；1 頁略過：第 3 頁沒有場景描述）");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();
  expect(posts).toBe(0);

  // 確認：一次 POST，後端排入 2 頁、略過第 3 頁
  const answered = page.waitForResponse((r) => r.url().endsWith(batchUrl) && r.request().method() === "POST");
  await all.click();
  await dialog.getByRole("button", { name: "產生 2 頁背景" }).click();
  const res = await answered;
  expect(res.status()).toBe(202);
  expect(res.request().postDataJSON()).toEqual({ style: "simple" });
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

test("產生所有背景：簡潔＋一頁有構圖草圖 → 對話框說明、送出簡潔，草圖頁後端用細緻", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 批次草圖頁"));
  const p1 = await createPage(request, book.id, "in a sunny park");
  const p2 = await createPage(request, book.id, "on a quiet beach");
  const p3 = await createPage(request, book.id, "in a green forest");
  await uploadSketch(request, p2.id); // 構圖強度預設 0.4 > 0 → 可用的草圖

  await page.goto(`/books/${book.id}/editor?page=${p1.id}`);
  const all = page.getByRole("button", { name: "產生所有背景" });
  await expect(all).toBeEnabled();
  await all.click();
  const dialog = page.getByRole("dialog", { name: "產生所有背景？" });
  await expect(dialog).toContainText("會為 3 頁產生背景（簡潔");
  await expect(dialog).toContainText("（簡潔");
  await expect(dialog).toContainText("有草圖的 1 頁用細緻");

  const batchUrl = `/api/books/${book.id}/backgrounds/batch`;
  const answered = page.waitForResponse((r) => r.url().endsWith(batchUrl) && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "產生 3 頁背景" }).click();
  const res = await answered;
  expect(res.status()).toBe(202);
  // 送出的是使用者選的風格；草圖頁改細緻由後端逐頁決定
  expect(res.request().postDataJSON()).toEqual({ style: "simple" });
  const body = (await res.json()) as { queued: { page_id: number; background_style: string }[]; skipped: unknown[] };
  expect(body.skipped).toEqual([]);
  const styles = Object.fromEntries(body.queued.map((g) => [g.page_id, g.background_style]));
  expect(styles).toEqual({ [p1.id]: "simple", [p2.id]: "detailed", [p3.id]: "simple" });
  await expect(page.getByText("已排入 3 頁背景")).toBeVisible();

  // 各頁背景卡片的風格標記與實際風格一致
  const history = page.getByRole("region", { name: "背景歷史" });
  const badgeOf = async (n: number, label: "簡潔" | "細緻") => {
    await pageButton(page, n).click();
    await expect(pageButton(page, n)).toHaveAttribute("aria-current", "true");
    await expect(history.getByRole("listitem")).toHaveCount(1);
    await expect(history.getByRole("listitem").getByText(label, { exact: true })).toBeVisible();
  };
  await badgeOf(1, "簡潔");
  await badgeOf(2, "細緻");
  await badgeOf(3, "簡潔");
});
