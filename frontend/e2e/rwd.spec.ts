import { expect, test, type Page } from "@playwright/test";
import { composePage, createBook, createPage, setCharacter, uniqueTitle } from "./seed";

const VIEWPORTS = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
] as const;

let bookId = 0;
let pageId = 0;
/** 已選背景＋角色動作＋位置，但還沒確認合成：合成分頁能用，線稿分頁還不能。 */
let draftPageId = 0;
const DRAFT_PLACEMENT = { cx: 0.45, cy: 0.66, height: 0.6, flip: false };

test.beforeAll(async ({ playwright }, testInfo) => {
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  const book = await createBook(request, uniqueTitle("e2e RWD"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "in a sunny park");
  await composePage(request, book.id, p.id, { confirm: true });
  // 再加兩頁（各 200 px 寬）：390 px 時頁面清單一定比畫面寬，才驗得出它是在清單內橫向捲動、不撐開整頁
  const draft = await createPage(request, book.id, "a very long scene description that should be truncated in the filmstrip");
  await composePage(request, book.id, draft.id, { placement: DRAFT_PLACEMENT });
  await createPage(request, book.id);
  bookId = book.id;
  pageId = p.id;
  draftPageId = draft.id;
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
      // 背景模式（預設）：已選背景 → 舞台顯示背景圖，下方是背景歷史托盤
      await expect(page.getByRole("button", { name: /^選 seed \d+ 為本頁背景$/ })).toHaveCount(1);
      await expect(page.getByRole("img", { name: "背景圖" })).toBeVisible();
      await expectNoHorizontalScroll(page);

      const list = film.getByRole("list");
      const overflowX = await list.evaluate((el) => getComputedStyle(el).overflowX);
      const panel = page.getByRole("complementary", { name: "檢查與設定" });
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

      // 畫構圖（畫板＋工具列）也不能撐出水平捲動
      await page.getByRole("button", { name: "畫構圖" }).click();
      await expect(page.getByRole("img", { name: /構圖畫板/ })).toBeVisible();
      await expectNoHorizontalScroll(page);

      // 角色模式：去背預覽＋常用動作按鈕
      await page.getByRole("tab", { name: "角色", exact: true }).click();
      await expect(page.getByRole("img", { name: "角色動作（去背預覽）" })).toBeVisible();
      await expect(page.getByRole("group", { name: "常用動作" })).toBeVisible();
      await expectNoHorizontalScroll(page);

      // 線稿模式（較寬的修線工具列）也不能撐出水平捲動
      await page.getByRole("tab", { name: /線稿與修線/ }).click();
      await expect(page.getByRole("toolbar", { name: "修線工具" })).toBeVisible();
      await expectNoHorizontalScroll(page);
    });

    test("合成分頁", async ({ page, request }) => {
      // 兩種寬度共用這一頁：先把位置放回 seed 的值（上一個寬度把角色移走了）
      const put = await request.put(`/api/pages/${draftPageId}/placement`, { data: DRAFT_PLACEMENT });
      expect(put.ok()).toBeTruthy();
      await page.goto(`/books/${bookId}/editor?page=${draftPageId}&mode=compose`);
      await expect(page.getByRole("tab", { name: "合成", exact: true })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("tab", { name: "線稿與修線" })).toHaveAttribute("aria-disabled", "true");
      const stage = page.getByTestId("compose-overlay");
      const character = page.getByRole("group", { name: "角色位置" });
      await expect(character).toHaveAttribute("data-ready", "true");
      await expect(page.getByText("水平 45%・垂直 66%・高度 60%")).toBeVisible();
      await expect(page.getByRole("button", { name: "確認合成" })).toBeVisible();
      await expectNoHorizontalScroll(page);

      // 舞台跟著容器寬度、角色框在舞台內（位置以百分比擺放）
      const s = (await stage.boundingBox())!;
      const c = (await character.boundingBox())!;
      expect(s.x).toBeGreaterThanOrEqual(0);
      expect(s.x + s.width).toBeLessThanOrEqual(vp.width);
      expect(c.x).toBeGreaterThanOrEqual(s.x - 1);
      expect(c.x + c.width).toBeLessThanOrEqual(s.x + s.width + 1);
      expect(Math.abs(c.x + c.width / 2 - (s.x + s.width * 0.45))).toBeLessThan(2);

      // 角色被移到右邊緣外（超出部分裁掉）也不能撐出水平捲動
      await character.focus();
      for (let i = 0; i < 12; i++) await page.keyboard.press("Shift+ArrowRight");
      await expect(page.getByText(/^水平 100%/)).toBeVisible();
      await expectNoHorizontalScroll(page);
      // 等位置寫完再結束，不讓晚到的 PUT 蓋掉下一個寬度開頭的重設
      await expect
        .poll(async () => {
          const pages = (await (await request.get(`/api/books/${bookId}/pages`)).json()) as {
            id: number;
            placement: { cx: number } | null;
          }[];
          return pages.find((x) => x.id === draftPageId)?.placement?.cx;
        })
        .toBe(1);
    });
  });
}
