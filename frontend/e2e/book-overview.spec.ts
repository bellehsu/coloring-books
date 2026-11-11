import { expect, test, type Page } from "@playwright/test";
import { createBook, createPage, getPages, setCharacter, uniqueTitle } from "./seed";

/**
 * B 整本書（spec §4.3）：兩頁有故事、一頁空白。還沒開始 → 「開始做這本書」→ 假後端（DemoEngine）產生背景與
 * 角色動作，worker 裡的頁面流程合成、補線、標成可以印 → 「整本做好了」的 toast 與分頁標題；卡片顯示可以印，
 * 篩選有效。390 px 沒有水平捲動，兩欄，「印出這本書」固定在底部。
 */
let bookId = 0;
let title = "";

test.beforeAll(async ({ playwright }, testInfo) => {
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  title = uniqueTitle("e2e 整本書");
  const book = await createBook(request, title);
  await setCharacter(request, book.id);
  await createPage(request, book.id, "陽光公園，有一棵大樹", "開心地揮手");
  await createPage(request, book.id, "安靜的海邊，有貝殼", "坐著看書");
  await createPage(request, book.id); // 沒寫故事：不會做，留在「還沒做完」
  bookId = book.id;
  await request.dispose();
});

async function expectNoHorizontalScroll(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, `scrollWidth ${scrollWidth} > innerWidth ${innerWidth}`).toBeLessThanOrEqual(innerWidth);
}

test("開始做這本書 → 做好了：卡片可以印、篩選、印出這本書先看一眼", async ({ page }) => {
  // 首頁的書卡連到 B
  await page.goto("/");
  await page.getByRole("link", { name: new RegExp(title) }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}$`));
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

  // 還沒開始：不說「明天繼續」
  const notStarted = page.getByRole("region", { name: "還沒開始做" });
  await expect(notStarted).toBeVisible();
  await expect(page.getByText(/明天繼續/)).toHaveCount(0);
  const produced = page.waitForResponse((r) => r.url().endsWith(`/api/books/${bookId}/produce`));
  await notStarted.getByRole("button", { name: "開始做這本書" }).click();
  expect((await produced).status()).toBe(202);

  // 做完：toast、分頁標題、狀態卡（空白那頁還沒做完）
  // toast 在常駐的 polite live region 裡
  await expect(page.getByTestId("toast-polite")).toContainText("做好了，還有 1 頁還沒做完", { timeout: 30_000 });
  await expect(page).toHaveTitle(/✓ 做好了/);
  const card = page.getByRole("region", { name: "還有 1 頁還沒做完" });
  await expect(card).toContainText("2 頁可以印・1 頁還沒做完");
  await expect(card.getByRole("list", { name: "每一頁的狀態" }).getByRole("listitem")).toHaveCount(3);

  // 頁面卡：前兩頁可以印（有線稿縮圖），連到 C
  const cards = page.getByRole("list", { name: "頁面" }).getByRole("link");
  await expect(cards).toHaveCount(3);
  const pages = await getPages(page.request, bookId);
  for (const i of [0, 1]) {
    await expect(cards.nth(i)).toContainText("可以印 ✓");
    await expect(cards.nth(i).getByRole("img", { name: `第 ${i + 1} 頁` })).toBeVisible();
    await expect(cards.nth(i)).toHaveAttribute("href", `/books/${bookId}/page/${pages[i].id}`);
  }
  await expect(cards.nth(2)).toContainText("還沒做完");

  // 篩選
  const filters = page.getByRole("group", { name: "篩選頁面" });
  await expect(filters.getByRole("button", { name: "全部 3" })).toHaveAttribute("aria-pressed", "true");
  await filters.getByRole("button", { name: "可以印 2" }).click();
  await expect(cards).toHaveCount(2);
  await filters.getByRole("button", { name: "還沒做完 1" }).click();
  await expect(cards).toHaveCount(1);
  await filters.getByRole("button", { name: "請看一下 0" }).click();
  await expect(cards).toHaveCount(0);
  await filters.getByRole("button", { name: "全部 3" }).click();
  await expect(cards).toHaveCount(3);

  // 印出這本書：先看一眼，再到 PDF 選項
  await page.getByRole("button", { name: "印出這本書" }).click();
  const sheet = page.getByRole("dialog", { name: "印出這本書" });
  await expect(sheet.getByRole("list", { name: "會印出來的頁面" }).getByRole("img")).toHaveCount(2);
  await sheet.getByRole("button", { name: "看好了，下一步 →" }).click();
  await expect(sheet.getByRole("button", { name: "下載 PDF（2 頁）" })).toBeEnabled();

  // 進階編輯仍然是舊的編輯器
  await sheet.getByRole("button", { name: "關閉" }).click();
  await page.getByRole("link", { name: "進階編輯" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}/editor`));
});

test.describe("390 px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("兩欄、底部固定印出這本書、沒有水平捲動、按鈕至少 44 px", async ({ page }) => {
    await page.goto(`/books/${bookId}`);
    const cards = page.getByRole("list", { name: "頁面" }).getByRole("link");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText("可以印 ✓");
    await expectNoHorizontalScroll(page);

    // 兩欄：第 1、2 張同一列，第 3 張在下一列
    const [a, b, c] = await Promise.all([0, 1, 2].map(async (i) => (await cards.nth(i).boundingBox())!));
    expect(Math.abs(a.y - b.y)).toBeLessThan(2);
    expect(b.x).toBeGreaterThan(a.x + a.width);
    expect(c.y).toBeGreaterThan(a.y + a.height);

    // 「印出這本書」固定在畫面底部
    const print = page.getByRole("button", { name: "印出這本書" });
    const box = (await print.boundingBox())!;
    expect(box.y + box.height).toBeGreaterThan(844 - 40);
    expect(box.y + box.height).toBeLessThanOrEqual(844);

    // 每個控制項至少 44 px 高
    const small = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>("main a, main button"))
        .map((el) => ({ text: el.textContent?.trim() ?? "", h: el.getBoundingClientRect().height }))
        .filter((x) => x.h < 44),
    );
    expect(small).toEqual([]);

    await print.click();
    await expect(page.getByRole("dialog", { name: "印出這本書" })).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});
