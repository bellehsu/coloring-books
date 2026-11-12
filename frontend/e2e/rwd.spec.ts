import { expect, test, type Page } from "@playwright/test";
import { composePage, createBook, createPage, setCharacter, uniqueTitle, drainQueueAfterAll } from "./seed";

drainQueueAfterAll();

const PHONE = { width: 390, height: 844 };
const VIEWPORTS = [PHONE, { width: 1440, height: 900 }] as const;
const PLACEMENT = { cx: 0.45, cy: 0.66, height: 0.6, flip: false };

let bookId = 0;
/** 已合成（有選定的合成圖）：補線能用。 */
let doneId = 0;
/** 背景＋角色動作已選；頁面流程合成後才改了位置，所以選定圖不是目前位置的合成圖（開頁時會自動合成）。 */
let draftId = 0;

test.beforeAll(async ({ playwright }, testInfo) => {
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  const book = await createBook(request, uniqueTitle("e2e RWD"));
  await setCharacter(request, book.id);
  const done = await createPage(request, book.id, "陽光公園", "揮手");
  await composePage(request, book.id, done.id, { compose: true });
  const draft = await createPage(
    request,
    book.id,
    "一段很長很長的場景描述，用來確認頁面清單或下拉選單不會被它撐開，也不會出現水平捲動",
    "坐著看書",
  );
  await composePage(request, book.id, draft.id, { placement: PLACEMENT });
  await createPage(request, book.id);
  bookId = book.id;
  doneId = done.id;
  draftId = draft.id;
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
  const phone = vp.width === PHONE.width;
  test.describe(`${vp.width}×${vp.height}`, () => {
    test.use({ viewport: vp });

    test("我的繪本", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "我的繪本" })).toBeVisible();
      await expect(page.getByRole("link", { name: /e2e RWD/ }).first()).toBeVisible();
      await expectNoHorizontalScroll(page);
    });

    test("主角與故事（舊的 /character、/story 轉到這裡）", async ({ page }) => {
      await page.goto(`/books/${bookId}/character`);
      await expect(page).toHaveURL(new RegExp(`/books/${bookId}/setup$`));
      await expect(page.getByRole("heading", { level: 1, name: "主角與故事" })).toBeVisible();
      await expect(page.getByRole("button", { name: "就是牠 ✓" })).toBeVisible();
      const row = page.getByRole("listitem", { name: "第 2 頁" });
      await expect(row).toContainText("一段很長很長的場景描述");
      const text = (await row.getByText(/一段很長很長/).boundingBox())!;
      const toggle = (await row.getByRole("group", { name: "第 2 頁的背景" }).boundingBox())!;
      if (phone) {
        // 手機：文字在上，背景切換在下面
        expect(toggle.y).toBeGreaterThanOrEqual(text.y + text.height - 1);
      } else {
        // 桌機：同一列，切換在右邊
        expect(toggle.x).toBeGreaterThan(text.x + text.width - 1);
      }
      await expectNoHorizontalScroll(page);

      await page.goto(`/books/${bookId}/story`);
      await expect(page).toHaveURL(new RegExp(`/books/${bookId}/setup$`));
      await page.getByRole("button", { name: "套用主題包" }).click();
      await expect(page.getByRole("dialog", { name: "套用主題包" })).toBeVisible();
      await expectNoHorizontalScroll(page);
    });

    test("頁面編輯器：① 構圖", async ({ page }) => {
      await page.goto(`/books/${bookId}/editor?page=${draftId}`);
      const film = page.getByRole("complementary", { name: "頁面" });
      const select = film.getByRole("combobox", { name: "選擇頁面" });
      const character = page.getByRole("group", { name: "角色位置" });
      await expect(character).toHaveAttribute("data-ready", "true");
      await expect(page.getByRole("status").filter({ hasText: "✓ 線稿已自動更新" })).toBeVisible();
      await expectNoHorizontalScroll(page);
      const stage = (await page.getByTestId("compose-overlay").boundingBox())!;
      expect(stage.x).toBeGreaterThanOrEqual(0);
      expect(stage.x + stage.width).toBeLessThanOrEqual(vp.width);

      if (phone) {
        // 頁面清單 → 頂端「第 N 頁 / M」下拉選單
        await expect(select).toBeVisible();
        await expect(select.locator("option:checked")).toHaveText("第 2 頁 / 3");
        await expect(film.getByRole("list")).toHaveCount(0);
        // 背景／角色動作是舞台下方的精簡卡片，點了才展開
        const bgCard = page.getByRole("button", { name: /^背景/, expanded: false });
        const poseCard = page.getByRole("button", { name: /^角色動作/, expanded: false });
        await expect(bgCard).toBeVisible();
        await expect(poseCard).toBeVisible();
        await expect(page.getByRole("button", { name: "換一張背景" })).toHaveCount(0);
        const card = (await bgCard.boundingBox())!;
        expect(card.y).toBeGreaterThan(stage.y + stage.height);
        // 兩張精簡卡片並排
        expect(Math.abs((await poseCard.boundingBox())!.y - card.y)).toBeLessThan(2);
        await bgCard.click();
        await expect(page.getByRole("button", { name: "換一張背景" })).toBeVisible();
        await expect(page.getByLabel("上傳背景圖")).toBeAttached();
        await expectNoHorizontalScroll(page);
        await page.getByRole("button", { name: "收合背景" }).click();
        await expect(page.getByRole("button", { name: "換一張背景" })).toHaveCount(0);
      } else {
        await expect(select).toHaveCount(0);
        await expect(film.getByRole("listitem")).toHaveCount(3);
        await expect(page.getByRole("button", { name: "換一張背景" })).toBeVisible();
        await expect(page.getByRole("button", { name: "換一張角色動作" })).toBeVisible();
      }

      // 舞台仍可拖曳：往右拖 5%（舞台寬）→ 存位置
      const box = (await character.boundingBox())!;
      const [sx, sy] = [box.x + box.width / 2, box.y + box.height / 2];
      const placed = page.waitForRequest((r) => /\/api\/pages\/\d+\/placement$/.test(r.url()) && r.method() === "PUT");
      await page.mouse.move(sx, sy);
      await page.mouse.down();
      await page.mouse.move(sx + stage.width * 0.025, sy, { steps: 4 });
      await page.mouse.move(sx + stage.width * 0.05, sy, { steps: 4 });
      await page.mouse.up();
      const cx = ((await placed).postDataJSON() as { cx: number }).cx;
      expect(cx).toBeGreaterThan(PLACEMENT.cx + 0.03);
      await expect(page.getByRole("status").filter({ hasText: "✓ 線稿已自動更新" })).toBeVisible();
      await expectNoHorizontalScroll(page);
      // 下一個寬度從同一個位置開始
      await page.request.put(`/api/pages/${draftId}/placement`, { data: PLACEMENT });
    });

    test("頁面編輯器：換頁與 ② 補線", async ({ page }) => {
      await page.goto(`/books/${bookId}/editor?page=${draftId}`);
      const film = page.getByRole("complementary", { name: "頁面" });
      if (phone) {
        await film.getByRole("combobox", { name: "選擇頁面" }).selectOption({ label: "第 1 頁 / 3" });
      } else {
        await film.getByRole("button", { name: /^第 1 頁 / }).click();
      }
      await expect(page).toHaveURL(new RegExp(`page=${doneId}`));
      await page.getByRole("tab", { name: /^② 補線/ }).click();
      await expect(page.getByRole("toolbar", { name: "修線工具" })).toBeVisible();
      await expect(page.getByTestId("finish-panel")).toBeVisible();
      await expectNoHorizontalScroll(page);
    });
  });
}

test.describe("動作卡片：表情與面向（390 px）", () => {
  test.use({ viewport: PHONE });

  test("加揮手、大笑、朝左，再改選難過：只剩一個表情；沒有水平捲動", async ({ page }) => {
    await page.goto(`/books/${bookId}/editor?page=${draftId}`);
    await page.getByRole("button", { name: /^角色動作/, expanded: false }).click();
    await page.getByText(/^動作：/).click(); // 展開 <details>
    const text = page.getByLabel("動作描述");
    await page.getByRole("group", { name: "常用動作" }).getByRole("button", { name: "揮手" }).click();
    const expr = page.getByRole("group", { name: "表情" });
    const facing = page.getByRole("group", { name: "面向" });
    await expr.getByRole("button", { name: "大笑" }).click();
    await facing.getByRole("button", { name: "朝左" }).click();
    await expr.getByRole("button", { name: "難過" }).click();
    const value = await text.inputValue();
    expect(value).toContain("with a sad expression");
    expect(value).not.toContain("laughing");
    expect(value).toContain("facing toward the left side of the picture");
    expect(value).toContain("waving one hand");
    await expect(page.getByText("臉會轉向，身體可能仍偏正面；不夠時可在構圖用左右翻轉")).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});
