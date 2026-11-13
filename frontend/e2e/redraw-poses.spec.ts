import { expect, test } from "@playwright/test";
import { createBook, createPage, getPages, ok, setCharacter, uniqueTitle, drainQueueAfterAll } from "./seed";

drainQueueAfterAll();

/**
 * 重畫主角（spec §4.4 #4A＋#4B）：3 頁的書「開始做這本書」做好 → B「選取多頁」勾 3 頁、每頁畫 1 張 →
 * 「重畫 3 頁的主角」→ 做完每頁都是請看一下、做好 3 / 3 頁 → C 第 1 頁畫過的角色動作有 2 張、新的使用中 →
 * 點舊的那張換上去 → ↶ 復原回到新的那張。假後端（DemoEngine）產生圖，worker 的頁面流程重新合成。
 */
interface Overview {
  pages: { id: number; state: string }[];
  progress: { queued: number; running: number };
}

test("B 選取多頁重畫主角 → 請看一下 → C 畫過的角色動作點一下就換、可以復原", async ({ page, request }) => {
  test.setTimeout(150_000);
  const book = await createBook(request, uniqueTitle("e2e 重畫主角"));
  await setCharacter(request, book.id);
  for (const [scene, action] of [
    ["陽光公園，有一棵大樹", "開心地揮手"],
    ["安靜的海邊，有貝殼", "坐著看書"],
    ["下雨的城市街道", "撐著雨傘走路"],
  ])
    await createPage(request, book.id, scene, action);
  const p1 = (await getPages(request, book.id)).sort((a, b) => a.id - b.id)[0];

  // 1. B：開始做這本書 → 每頁可以印
  await page.goto(`/books/${book.id}`);
  await page.getByRole("button", { name: "開始做這本書" }).click();
  await expect(page.getByRole("region", { name: "整本做好了！" })).toBeVisible({ timeout: 60_000 });
  const cards = page.getByRole("list", { name: "頁面" }).getByRole("listitem");
  await expect(cards).toHaveCount(3);
  for (let i = 0; i < 3; i++) await expect(cards.nth(i)).toContainText("可以印 ✓");

  // 2. 選取多頁 → 勾 3 頁、每頁畫 1 張 → 重畫 3 頁的主角
  await page.getByRole("button", { name: "選取多頁" }).click();
  for (let n = 1; n <= 3; n++) await page.getByRole("checkbox", { name: `選第 ${n} 頁` }).check();
  const bar = page.getByRole("region", { name: "重畫選取的頁" });
  await expect(bar.getByRole("radio", { name: "1" })).toHaveAttribute("aria-checked", "true");
  await bar.getByRole("button", { name: "重畫 3 頁的主角" }).click();

  // 3. 等到沒有頁在做：每頁請看一下，做好 3 / 3 頁
  await expect
    .poll(
      async () => {
        const o = await ok<Overview>(await request.get(`/api/books/${book.id}/overview`));
        return o.progress.queued + o.progress.running === 0 && o.pages.every((p) => p.state !== "working");
      },
      { timeout: 60_000 },
    )
    .toBe(true);
  for (let i = 0; i < 3; i++) await expect(cards.nth(i)).toContainText("請看一下");
  await expect(page.getByText("做好 3 / 3 頁")).toBeVisible();

  // 4. C 第 1 頁：畫過 2 張，新的那張使用中
  await page.goto(`/books/${book.id}/page/${p1.id}`);
  const poses = page.getByRole("list", { name: "畫過的角色動作" }).getByRole("button");
  await expect(poses).toHaveCount(2);
  await expect(poses.nth(0)).toHaveAttribute("aria-pressed", "true");
  await expect(poses.nth(0)).toContainText("使用中");
  await expect(page.getByText("這頁畫過 2 張・點一下就換")).toBeVisible();

  // 5. 點舊的那張 → 換上去；↶ 復原 → 新的那張又是使用中
  await poses.nth(1).click();
  await expect(poses.nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(poses.nth(0)).toHaveAttribute("aria-pressed", "false");
  const undo = page.getByRole("button", { name: "復原" });
  await expect(undo).toBeEnabled({ timeout: 30_000 });
  await undo.click();
  await expect(poses.nth(0)).toHaveAttribute("aria-pressed", "true", { timeout: 30_000 });
  await expect(poses.nth(1)).toHaveAttribute("aria-pressed", "false");
});
