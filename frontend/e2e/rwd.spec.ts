import { expect, test, type Page } from "@playwright/test";
import { createBook, createPage, generateAndSelect, setCharacter, uniqueTitle } from "./seed";

const VIEWPORTS = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
] as const;

let bookId = 0;
let pageId = 0;

test.beforeAll(async ({ playwright }, testInfo) => {
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  const book = await createBook(request, uniqueTitle("e2e RWD"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "in a sunny park");
  await generateAndSelect(request, book.id, p.id, 2);
  // 再加兩頁（各 200 px 寬）：390 px 時頁面清單一定比畫面寬，才驗得出它是在清單內橫向捲動、不撐開整頁
  await createPage(request, book.id, "a very long scene description that should be truncated in the filmstrip");
  await createPage(request, book.id);
  bookId = book.id;
  pageId = p.id;
  await request.dispose();
});

async function expectNoHorizontalScroll(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, `scrollWidth ${scrollWidth} > innerWidth ${innerWidth}`).toBeLessThanOrEqual(innerWidth);
}

for (const vp of VIEWPORTS) {
  test.describe(`${vp.width}×${vp.height}`, () => {
    test.use({ viewport: vp });

    test("我的繪本", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "我的繪本" })).toBeVisible();
      await expect(page.getByRole("link", { name: /e2e RWD/ }).first()).toBeVisible();
      await expectNoHorizontalScroll(page);
    });

    test("角色工作室", async ({ page }) => {
      await page.goto(`/books/${bookId}/character`);
      await expect(page.getByRole("img", { name: "已設定的角色" })).toBeVisible();
      await expect(page.getByRole("link", { name: /前往頁面編輯器/ })).toBeVisible();
      await expectNoHorizontalScroll(page);
    });

    test("頁面編輯器", async ({ page }) => {
      await page.goto(`/books/${bookId}/editor?page=${pageId}`);
      const film = page.getByRole("complementary", { name: "頁面" });
      await expect(film.getByRole("button", { name: /第 1 頁/ }).first()).toBeVisible();
      await expect(film.getByRole("listitem")).toHaveCount(3);
      await expect(page.getByRole("button", { name: /^選 seed \d+ 為本頁圖片$/ })).toHaveCount(2);
      await expect(page.getByRole("img", { name: /構圖畫板/ })).toBeVisible();
      await expectNoHorizontalScroll(page);

      const list = film.getByRole("list");
      const overflowX = await list.evaluate((el) => getComputedStyle(el).overflowX);
      const panel = page.getByRole("region", { name: "生成" });
      if (vp.width <= 760) {
        // 單欄：頁面清單橫向捲動，檢查面板在工作區下方
        expect(overflowX).toBe("auto");
        expect(await list.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
        const work = (await page.getByRole("region", { name: "工作區" }).boundingBox())!;
        const insp = (await panel.boundingBox())!;
        expect(insp.y).toBeGreaterThanOrEqual(work.y + work.height - 1);
      } else {
        // 三欄：頁面清單在左、檢查面板在右，同一列
        expect(overflowX).not.toBe("auto");
        const filmBox = (await film.boundingBox())!;
        const insp = (await panel.boundingBox())!;
        expect(insp.x).toBeGreaterThan(filmBox.x + filmBox.width);
      }

      // 線稿模式（較寬的修線工具列）也不能撐出水平捲動
      await page.getByRole("tab", { name: /線稿與修線/ }).click();
      await expect(page.getByRole("toolbar", { name: "修線工具" })).toBeVisible();
      await expectNoHorizontalScroll(page);
    });
  });
}
