import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { composePage, createBook, createPage, getPages, setCharacter, uniqueTitle, drainQueueAfterAll } from "./seed";

drainQueueAfterAll();

/**
 * C 處理一頁（spec §4.4）：從 B 打開做好的一頁 → 背景「AI 再畫一張」新圖換上去 → 復原回到舊的 → 重做 →
 * 畫一筆、線條「還原成自動畫的那張」→「這頁可以印了 ✓」到下一頁。390 px 單欄、沒有水平捲動、按鈕至少 44 px。
 * 假後端（DemoEngine）產生圖，worker 的頁面流程重新合成。
 */
let bookId = 0;
let pageIds: number[] = [];

interface PageOut {
  id: number;
  background_generation_id: number | null;
  status: string;
  recompose_pending: boolean;
  has_user_strokes: boolean;
  can_undo: boolean;
  can_redo: boolean;
}

test.beforeAll(async ({ playwright }, testInfo) => {
  test.setTimeout(120_000);
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  const book = await createBook(request, uniqueTitle("e2e 處理一頁"));
  await setCharacter(request, book.id);
  const p1 = await createPage(request, book.id, "陽光公園，有一棵大樹", "開心地揮手");
  const p2 = await createPage(request, book.id, "安靜的海邊，有貝殼", "坐著看書");
  await composePage(request, book.id, p1.id);
  await composePage(request, book.id, p2.id);
  bookId = book.id;
  pageIds = [p1.id, p2.id];
  await request.dispose();
});

async function pageOut(request: APIRequestContext, id: number): Promise<PageOut> {
  return (await getPages(request, bookId)).find((p) => p.id === id) as unknown as PageOut;
}

/** 等頁面流程跑完：可以印、沒有等著重新合成。 */
async function settled(request: APIRequestContext, id: number) {
  await expect
    .poll(async () => {
      const p = await pageOut(request, id);
      return p.status === "passed" && !p.recompose_pending;
    }, { timeout: 30_000 })
    .toBe(true);
}

async function expectNoHorizontalScroll(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, `scrollWidth ${scrollWidth} > innerWidth ${innerWidth}`).toBeLessThanOrEqual(innerWidth);
}

async function smallControls(page: Page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>(
      "main a, main button, main label:has(input[type=file]), main label:has(input[type=checkbox])",
    ))
      .filter((el) => el.offsetParent !== null)
      .map((el) => ({ text: el.textContent?.trim() ?? "", h: el.getBoundingClientRect().height }))
      .filter((x) => x.h < 44),
  );
}

test("換背景 → 復原 → 重做 → 畫線再還原 → 這頁可以印了到下一頁", async ({ page, request }) => {
  test.setTimeout(150_000);
  const [p1, p2] = pageIds;
  const before = await pageOut(request, p1);
  const oldBg = before.background_generation_id!;

  // 1. 從 B 打開第 1 頁
  await page.goto(`/books/${bookId}`);
  await page.getByRole("list", { name: "頁面" }).getByRole("link").first().click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}/page/${p1}$`));
  await expect(page.getByRole("heading", { level: 1, name: /^第 1 頁/ })).toBeVisible();
  await expect(page.getByText("可以印 ✓")).toBeVisible();
  await expect(page.getByRole("img", { name: "要印的線稿" })).toBeVisible();
  const undo = page.getByRole("button", { name: "復原", exact: true });
  const redo = page.getByRole("button", { name: "重做", exact: true });
  await expect(undo).toBeDisabled();
  await expect(redo).toBeDisabled();

  // 2. 背景「AI 再畫一張」：新圖好了就換上去
  const bgRow = page.getByRole("listitem", { name: "背景" });
  const queued = page.waitForResponse((r) => /\/backgrounds\?oneclick=true$/.test(r.url()) && r.request().method() === "POST");
  await bgRow.getByRole("button", { name: "AI 再畫一張" }).click();
  const newBg = ((await (await queued).json()) as { id: number }).id;
  await expect.poll(async () => (await pageOut(request, p1)).background_generation_id, { timeout: 30_000 }).toBe(newBg);
  await settled(request, p1);
  await page.getByRole("radio", { name: "彩色原圖" }).click();
  const bgImg = page.getByRole("img", { name: "背景圖" });
  await expect(bgImg).toHaveAttribute("src", `/api/generations/${newBg}/image`);
  await expect(undo).toBeEnabled();

  // 3. 復原：舊的背景回來
  await undo.click();
  await expect(bgImg).toHaveAttribute("src", `/api/generations/${oldBg}/image`);
  expect((await pageOut(request, p1)).background_generation_id).toBe(oldBg);
  await expect(redo).toBeEnabled();
  await settled(request, p1);

  // 4. 重做：又是新的那張
  await redo.click();
  await expect(bgImg).toHaveAttribute("src", `/api/generations/${newBg}/image`);
  await settled(request, p1);

  // 5. 畫一筆（user 層）；拖角色清掉、復原帶回來；再「還原成自動畫的那張」清掉
  await page.getByRole("radio", { name: "要印的線稿" }).click();
  const linesRow = page.getByRole("listitem", { name: "線條" });
  const pen = linesRow.getByRole("button", { name: "畫筆" });
  await expect(pen).toBeEnabled({ timeout: 30_000 });
  await pen.click();
  const paper = page.locator('[data-part="paper"]');
  await expect(paper.getByRole("img")).toBeVisible();
  const box = (await paper.boundingBox())!;
  const drawn = page.waitForResponse((r) => /\/lineart\/edits\?oneclick=true$/.test(r.url()) && r.request().method() === "PUT");
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.35, { steps: 4 });
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4, { steps: 4 });
  await page.mouse.up();
  expect((await drawn).status()).toBe(200);
  await page.getByRole("button", { name: "畫好了" }).click();
  await expect.poll(async () => (await pageOut(request, p1)).has_user_strokes).toBe(true);
  const restoreLines = linesRow.getByRole("button", { name: /還原成自動畫的那張/ });
  await expect(restoreLines).toBeVisible();
  await settled(request, p1);

  // 5b. 拖角色 → 重新合成，筆畫清掉（不跳對話框）→ 復原 → 筆畫回來
  await page.getByRole("radio", { name: "彩色原圖" }).click();
  const character = page.getByRole("group", { name: "角色位置" });
  await expect(character).toHaveAttribute("data-ready", "true");
  const cb = (await character.boundingBox())!;
  const ov = (await page.getByTestId("compose-overlay").boundingBox())!;
  const placed = page.waitForResponse((r) => /\/placement\?oneclick=true$/.test(r.url()) && r.request().method() === "PUT");
  await page.mouse.move(cb.x + cb.width / 2, cb.y + cb.height / 2);
  await page.mouse.down();
  await page.mouse.move(cb.x + cb.width / 2 - ov.width * 0.05, cb.y + cb.height / 2, { steps: 4 });
  await page.mouse.move(cb.x + cb.width / 2 - ov.width * 0.08, cb.y + cb.height / 2, { steps: 4 });
  await page.mouse.up();
  expect((await placed).status()).toBe(200);
  await expect(restoreLines).toHaveCount(0); // 筆畫清掉了
  expect((await pageOut(request, p1)).has_user_strokes).toBe(false);
  await settled(request, p1);
  await expect(undo).toBeEnabled();
  await undo.click();
  await expect(restoreLines).toBeVisible(); // 筆畫回來了
  await settled(request, p1);
  expect((await pageOut(request, p1)).has_user_strokes).toBe(true);
  await page.getByRole("radio", { name: "要印的線稿" }).click();
  await restoreLines.click();
  await expect(restoreLines).toHaveCount(0);
  expect((await pageOut(request, p1)).has_user_strokes).toBe(false);
  await settled(request, p1);

  // 6. 這頁可以印了 → 下一頁
  await page.getByRole("button", { name: "這頁可以印了 ✓" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}/page/${p2}$`));
  await expect(page.getByRole("heading", { level: 1, name: /^第 2 頁/ })).toBeVisible();
  // 最後一頁再按 → 回到整本書
  await page.getByRole("button", { name: "這頁可以印了 ✓" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}$`));

  // 進階編輯連到舊的編輯器（這一頁）
  await page.goto(`/books/${bookId}/page/${p2}`);
  await expect(page.getByRole("link", { name: "進階編輯" })).toHaveAttribute("href", `/books/${bookId}/editor?page=${p2}`);
});

test.describe("390 px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("單欄：預覽在上、修改列在下，沒有水平捲動，按鈕至少 44 px", async ({ page, request }) => {
    const [p1] = pageIds;
    await settled(request, p1);
    await page.goto(`/books/${bookId}/page/${p1}`);
    const preview = page.getByRole("region", { name: "預覽" });
    await expect(page.getByRole("img", { name: "要印的線稿" })).toBeVisible();
    const items = page.getByRole("list", { name: "這一頁的東西" });
    const [pb, ib] = [(await preview.boundingBox())!, (await items.boundingBox())!];
    expect(ib.y).toBeGreaterThan(pb.y + pb.height - 1);
    expect(Math.abs(ib.x - pb.x)).toBeLessThan(2);
    await expectNoHorizontalScroll(page);
    expect(await smallControls(page)).toEqual([]);

    // 展開改動作（表情、面向晶片）與改字，彩色原圖，畫筆：都不超出、都夠大
    await items.getByRole("listitem", { name: "角色動作" }).getByRole("button", { name: "改動作" }).click();
    await expect(page.getByRole("group", { name: "表情" })).toBeVisible();
    await items.getByRole("listitem", { name: "圖下方的字" }).getByRole("button", { name: "改字" }).click();
    await expectNoHorizontalScroll(page);
    expect(await smallControls(page)).toEqual([]);

    await page.getByRole("radio", { name: "彩色原圖" }).click();
    await expect(page.getByRole("img", { name: "背景圖" })).toBeVisible();
    await expectNoHorizontalScroll(page);
    expect(await smallControls(page)).toEqual([]);

    await items.getByRole("listitem", { name: "線條" }).getByRole("button", { name: "畫筆" }).click();
    await expect(page.locator('[data-part="paper"]')).toBeVisible();
    await expectNoHorizontalScroll(page);
    expect(await smallControls(page)).toEqual([]);
  });
});
