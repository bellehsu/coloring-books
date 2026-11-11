import { expect, test, type Page } from "@playwright/test";
import { composePage, createBook, createPage, ok, setCharacter, uniqueTitle } from "./seed";

/**
 * 圖下方的字要印哪幾行（Phase 13b）：C 的「中文」「英文」開關（多選）→ print.png 跟著變；兩個都關時文字帶
 * 照留、整條空白（線稿不放大回全尺寸）；「把這個選擇套用到整本書」讓沒有字的頁也留白。390 px 沒有水平捲動。
 * 假後端（DemoEngine）產生圖，頁面流程合成；print.png 在瀏覽器裡用 canvas 解碼比對像素列。
 */
let bookId = 0;
let withText = 0;
let noText = 0;

test.beforeAll(async ({ playwright }, testInfo) => {
  test.setTimeout(120_000);
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  const book = await createBook(request, uniqueTitle("e2e 圖下方的字"));
  await setCharacter(request, book.id);
  const p1 = await createPage(request, book.id, "陽光公園，有一棵大樹", "開心地揮手");
  const p2 = await createPage(request, book.id, "安靜的海邊，有貝殼", "坐著看書");
  await composePage(request, book.id, p1.id);
  await composePage(request, book.id, p2.id);
  await ok(await request.patch(`/api/pages/${p1.id}`, { data: { caption_zh: "消防車", caption_en: "Fire Engine" } }));
  bookId = book.id;
  withText = p1.id;
  noText = p2.id;
  await request.dispose();
});

interface Rows {
  width: number;
  height: number;
  /** 每一列有沒有黑點 */
  ink: boolean[];
  /** 每一列的內容（比對用） */
  sig: string[];
}

/** 在瀏覽器裡解碼 print.png（no-store，拿最新的），回傳每一列的黑點與內容摘要。 */
async function printRows(page: Page, pageId: number): Promise<Rows> {
  return page.evaluate(async (id) => {
    const res = await fetch(`/api/pages/${id}/print.png?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`print.png ${res.status}`);
    const bmp = await createImageBitmap(await res.blob());
    const canvas = document.createElement("canvas");
    canvas.width = bmp.width;
    canvas.height = bmp.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    ctx.drawImage(bmp, 0, 0);
    const data = ctx.getImageData(0, 0, bmp.width, bmp.height).data;
    const ink: boolean[] = [];
    const sig: string[] = [];
    for (let y = 0; y < bmp.height; y++) {
      let any = false;
      let h = 0;
      for (let x = 0; x < bmp.width; x++) {
        const dark = data[(y * bmp.width + x) * 4] < 128;
        if (dark) {
          any = true;
          h = (h * 31 + x + 1) | 0;
        }
      }
      ink.push(any);
      sig.push(`${h}`);
    }
    return { width: bmp.width, height: bmp.height, ink, sig };
  }, pageId);
}

const lastInk = (r: Rows) => r.ink.lastIndexOf(true);
const firstDiff = (a: Rows, b: Rows) => a.sig.findIndex((s, i) => s !== b.sig[i]);
const same = (a: Rows, b: Rows) => firstDiff(a, b) === -1;

async function expectNoHorizontalScroll(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, `scrollWidth ${scrollWidth} > innerWidth ${innerWidth}`).toBeLessThanOrEqual(innerWidth);
}

test("英文關掉 → print.png 變了；兩個都關 → 文字帶照留、整條空白；套用到整本書", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto(`/books/${bookId}/page/${withText}`);
  const row = page.getByRole("listitem", { name: "圖下方的字" });
  const group = row.getByRole("group", { name: "要印哪幾行" });
  const zh = group.getByRole("button", { name: /中文/ });
  const en = group.getByRole("button", { name: /英文/ });
  await expect(zh).toHaveAttribute("aria-pressed", "true");
  await expect(en).toHaveAttribute("aria-pressed", "true");
  await expect(row.getByText("兩個都不選＝圖下方留白，讓小朋友自己寫")).toBeVisible();
  const preview = page.getByRole("img", { name: "要印的線稿" });
  await expect(preview).toBeVisible();

  const both = await printRows(page, withText);
  const plainOther = await printRows(page, noText); // 沒有字的頁：沒有文字帶、線稿全尺寸

  // 1. 英文關掉：存起來（oneclick）、預覽換網址、print.png 只剩中文那行
  const src0 = await preview.getAttribute("src");
  let saved = page.waitForResponse((r) => /\/api\/pages\/\d+\?oneclick=true$/.test(r.url()) && r.request().method() === "PATCH");
  await en.click();
  expect((await saved).status()).toBe(200);
  await expect(en).toHaveAttribute("aria-pressed", "false");
  await expect(preview).not.toHaveAttribute("src", src0!);
  await expect(row.getByText("消防車", { exact: true })).toBeVisible();
  const onlyZh = await printRows(page, withText);
  expect(same(onlyZh, both)).toBe(false);
  const d1 = firstDiff(onlyZh, both);
  expect(d1 / both.height).toBeGreaterThan(0.75); // 只有文字帶變了，線稿沒動

  // 2. 中文也關掉：文字帶整條空白，線稿位置和有字時一模一樣（沒有放大回全尺寸）
  saved = page.waitForResponse((r) => /\/api\/pages\/\d+\?oneclick=true$/.test(r.url()) && r.request().method() === "PATCH");
  await zh.click();
  expect((await saved).status()).toBe(200);
  await expect(zh).toHaveAttribute("aria-pressed", "false");
  await expect(row.getByText("不印字・圖下方留白")).toBeVisible();
  const blank = await printRows(page, withText);
  expect(same(blank, both)).toBe(false);
  expect(same(blank, onlyZh)).toBe(false);
  const band = firstDiff(blank, both);
  expect(band / both.height).toBeGreaterThan(0.75);
  expect(blank.ink.slice(band).some(Boolean)).toBe(false); // 帶（與下方）沒有任何黑點
  expect(both.ink.slice(band).some(Boolean)).toBe(true); // 兩個都開時帶裡有字
  expect(lastInk(blank)).toBeLessThan(band);

  // 3. 套用到整本書：沒有字的另一頁也留白（線稿縮到帶上方，和原本全尺寸的不同）
  const applied = page.waitForResponse((r) => r.url().endsWith(`/api/books/${bookId}/caption-display`));
  await row.getByRole("button", { name: "把這個選擇套用到整本書" }).click();
  expect((await applied).status()).toBe(200);
  await expect(page.getByText("整本書都改成「圖下方留白」了")).toBeVisible();
  const blankOther = await printRows(page, noText);
  expect(same(blankOther, plainOther)).toBe(false);
  expect(lastInk(blankOther)).toBeLessThan(lastInk(plainOther));
  expect(blankOther.ink.slice(band).some(Boolean)).toBe(false);

  // 4. 復原：回到只印中文
  await page.getByRole("button", { name: "復原", exact: true }).click();
  await expect(zh).toHaveAttribute("aria-pressed", "true");
  await expect(en).toHaveAttribute("aria-pressed", "false");
  expect(same(await printRows(page, withText), onlyZh)).toBe(true);

  // B 的卡片跟著顯示：第 1 頁印中文、第 2 頁圖下方留白
  await page.goto(`/books/${bookId}`);
  const cards = page.getByRole("list", { name: "頁面" }).getByRole("link");
  await expect(cards.nth(0)).toContainText("消防車");
  await expect(cards.nth(1)).toContainText("圖下方留白");
});

test.describe("390 px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("開關、提示與套用連結：沒有水平捲動，按鈕至少 44 px", async ({ page }) => {
    await page.goto(`/books/${bookId}/page/${withText}`);
    const row = page.getByRole("listitem", { name: "圖下方的字" });
    await expect(row.getByRole("group", { name: "要印哪幾行" })).toBeVisible();
    await row.getByRole("button", { name: "改字" }).click();
    await expect(row.getByRole("textbox", { name: "中文字" })).toBeVisible();
    await expectNoHorizontalScroll(page);
    const small = await row.evaluate((el) =>
      Array.from(el.querySelectorAll<HTMLElement>("button"))
        .filter((b) => b.offsetParent !== null)
        .map((b) => ({ text: b.textContent?.trim() ?? "", h: b.getBoundingClientRect().height }))
        .filter((x) => x.h < 44),
    );
    expect(small).toEqual([]);
    const rb = (await row.boundingBox())!;
    expect(rb.x + rb.width).toBeLessThanOrEqual(390);
  });
});
