import { expect, test, type APIRequestContext } from "@playwright/test";
import { composePage, createBook, createPage, drainQueueAfterAll, getPages, setCharacter, uniqueTitle } from "./seed";

drainQueueAfterAll();

/**
 * 清掉框外雜物（spec 2026-11-24-background-cleanup §5.5）：一般擺放的頁按鈕停用、說明要有框；
 * 改成肖像（位置帶 frame）之後 C 的背景列按下去（?oneclick=true）→ 清好的背景換上去；
 * 進階編輯的背景縮圖標「清過」，卡片上同一顆按鈕可以按。假後端（DemoEngine）產生清理的圖。
 */
const FRAME_PLACEMENT = { cx: 0.5, cy: 0.59, height: 0.42, flip: false, frame: { x: 0.5, y: 0.55 }, autofit: true };

interface PageOut {
  id: number;
  background_generation_id: number | null;
  status: string;
  recompose_pending: boolean;
}

async function pageOut(request: APIRequestContext, bookId: number, id: number): Promise<PageOut> {
  return (await getPages(request, bookId)).find((p) => p.id === id) as unknown as PageOut;
}

test("一般擺放不能清；肖像頁在 C 按「清掉框外雜物」→ 清好的換上去，進階編輯的縮圖標「清過」", async ({ page, request }) => {
  test.setTimeout(150_000);
  const book = await createBook(request, uniqueTitle("e2e 清掉框外雜物"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "華麗的相框", "揮手");
  await composePage(request, book.id, p.id);
  const oldBg = (await pageOut(request, book.id, p.id)).background_generation_id!;

  // 1. 一般擺放：停用、說明
  await page.goto(`/books/${book.id}/page/${p.id}`);
  const bgRow = page.getByRole("listitem", { name: "背景" });
  const clean = bgRow.getByRole("button", { name: "清掉框外雜物" });
  await expect(clean).toBeDisabled();
  await expect(bgRow.getByText("有框的背景（肖像模式）才能清")).toBeVisible();

  // 2. 改成肖像（存位置）→ 可以按；按下去用 oneclick 排清理，好了換上去
  await expect(
    request.put(`/api/pages/${p.id}/placement`, { data: FRAME_PLACEMENT }).then((r) => r.ok()),
  ).resolves.toBe(true);
  await expect.poll(async () => (await pageOut(request, book.id, p.id)).recompose_pending, { timeout: 30_000 }).toBe(false);
  await page.reload();
  await expect(clean).toBeEnabled({ timeout: 30_000 });
  await expect(bgRow.getByText("有框的背景（肖像模式）才能清")).toHaveCount(0);
  const queued = page.waitForResponse(
    (r) => /\/backgrounds\/cleanup\?oneclick=true$/.test(r.url()) && r.request().method() === "POST",
  );
  await clean.click();
  const res = await queued;
  expect(res.status()).toBe(202);
  const cleaned = (await res.json()) as { id: number; source_generation_id: number | null };
  expect(cleaned.source_generation_id).toBe(oldBg);
  await expect.poll(async () => (await pageOut(request, book.id, p.id)).background_generation_id, { timeout: 30_000 }).toBe(cleaned.id);
  // 做完了：不再說正在清、按鈕又可以按（假後端的圖沒有框，頁面標「找不到框」的軟提示，仍可以印）
  await expect(bgRow.getByText(/正在清掉框外雜物/)).toHaveCount(0, { timeout: 30_000 });
  await expect(clean).toBeEnabled({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "這頁可以印了 ✓" })).toBeEnabled({ timeout: 30_000 });

  // 3. 進階編輯：背景縮圖標「清過」，卡片上也有同一顆按鈕
  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  const card = page.getByRole("region", { name: "背景" });
  const thumb = card.getByRole("radio", { name: "背景第 2 張（清過）" });
  await expect(thumb).toHaveAttribute("aria-checked", "true");
  await expect(card.getByRole("radio", { name: "背景第 1 張" })).toBeVisible();
  await expect(card.getByRole("button", { name: "清掉框外雜物" })).toBeEnabled();
});
