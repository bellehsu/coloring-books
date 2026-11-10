import { expect, test, type Page } from "@playwright/test";
import { createBook, createPage, uniqueTitle, uploadSketch } from "./seed";

const BACKGROUNDS = /\/api\/pages\/\d+\/backgrounds$/;
const SIMPLE_HINT = "約 20 秒，線條少、適合小小孩";
const DETAILED_HINT = "約 10 秒，細節多，可以照構圖草圖";
const SKETCH_NOTE = "有構圖草圖時用細緻，才會照草圖構圖";

/** 背景歷史裡 seed 的那張卡（成功後才有「選 seed N 為本頁背景」按鈕）。 */
const bgCard = (page: Page, seed: number) =>
  page
    .getByRole("region", { name: "背景歷史" })
    .getByRole("listitem")
    .filter({ has: page.getByRole("button", { name: `選 seed ${seed} 為本頁背景`, exact: true }) });

type Style = "simple" | "detailed";

/**
 * 按「產生背景」：送出的 style＝使用者選的（sent），後端記錄的實際風格＝effective（草圖頁由後端改細緻）；
 * 回傳新 generation 的 seed。
 */
async function generateBackground(page: Page, sent: Style, effective: Style = sent): Promise<number> {
  const posted = page.waitForRequest((r) => BACKGROUNDS.test(r.url()) && r.method() === "POST");
  const answered = page.waitForResponse((r) => BACKGROUNDS.test(r.url()) && r.request().method() === "POST");
  await page.getByRole("button", { name: "產生背景" }).click();
  expect((await posted).postDataJSON()).toEqual({ style: sent });
  const res = await answered;
  expect(res.status()).toBe(202);
  const gen = (await res.json()) as { seed: number; background_style: string };
  expect(gen.background_style).toBe(effective);
  return gen.seed;
}

test("背景風格：預設簡潔 → 產生 → 切細緻再產生 → 卡片標記；重新整理後仍是細緻", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 背景風格"));
  const p = await createPage(request, book.id, "in a sunny park");
  await page.goto(`/books/${book.id}/editor?page=${p.id}`);

  const styles = page.getByRole("radiogroup", { name: "背景風格" });
  const simple = styles.getByRole("radio", { name: "簡潔" });
  const detailed = styles.getByRole("radio", { name: "細緻" });
  await expect(simple).toHaveAttribute("aria-checked", "true");
  await expect(simple).toBeEnabled();
  await expect(detailed).toHaveAttribute("aria-checked", "false");
  await expect(styles).toHaveAccessibleDescription(SIMPLE_HINT);
  await expect(page.getByText(SKETCH_NOTE)).toHaveCount(0);

  const simpleSeed = await generateBackground(page, "simple");
  await expect(bgCard(page, simpleSeed).getByText("簡潔", { exact: true })).toBeVisible();

  await detailed.click();
  await expect(detailed).toHaveAttribute("aria-checked", "true");
  await expect(styles).toHaveAccessibleDescription(DETAILED_HINT);
  expect(await page.evaluate(() => localStorage.getItem("coloring.bgStyle"))).toBe("detailed");
  const detailedSeed = await generateBackground(page, "detailed");
  await expect(bgCard(page, detailedSeed).getByText("細緻", { exact: true })).toBeVisible();
  await expect(bgCard(page, simpleSeed).getByText("簡潔", { exact: true })).toBeVisible();

  await page.reload();
  await expect(detailed).toHaveAttribute("aria-checked", "true");
  await expect(simple).toHaveAttribute("aria-checked", "false");
  await expect(simple).toBeEnabled();
});

test("背景風格：有構圖草圖的頁「簡潔」停用、用細緻產生；強度拉到 0 → 簡潔恢復", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 草圖用細緻"));
  const p = await createPage(request, book.id, "on a quiet beach");
  await uploadSketch(request, p.id);
  await page.goto(`/books/${book.id}/editor?page=${p.id}`);

  const styles = page.getByRole("radiogroup", { name: "背景風格" });
  const simple = styles.getByRole("radio", { name: "簡潔" });
  const detailed = styles.getByRole("radio", { name: "細緻" });
  await expect(simple).toBeDisabled();
  await expect(detailed).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(SKETCH_NOTE)).toBeVisible();
  await expect(styles).toHaveAccessibleDescription(`${DETAILED_HINT} ${SKETCH_NOTE}`);
  // 被迫細緻不改寫記住的選擇
  expect(await page.evaluate(() => localStorage.getItem("coloring.bgStyle"))).toBeNull();

  // 送出使用者選的簡潔（background_style_requested），後端因草圖改用細緻
  const seed = await generateBackground(page, "simple", "detailed");
  await expect(bgCard(page, seed).getByText("細緻", { exact: true })).toBeVisible();

  // 構圖強度 0＝不參考草圖 → 回到記住的簡潔
  const strength = page.getByLabel(/構圖強度/);
  await strength.focus();
  await strength.press("Home");
  await expect(simple).toBeEnabled();
  await expect(simple).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(SKETCH_NOTE)).toHaveCount(0);
});
