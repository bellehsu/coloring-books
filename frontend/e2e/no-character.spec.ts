import { expect, test } from "@playwright/test";
import { createBook, drainQueueAfterAll, setCharacter, uniqueTitle } from "./seed";

drainQueueAfterAll();

/**
 * 不放主角（spec 2026-11-26-no-character-pages）：設定頁套用恐龍包 → 勾「不放主角」→ 開始做 →
 * 整本做好 → 打開第 1 頁（一鍵處理頁）沒有角色動作列、說「這頁不放主角」→ 勾回「放主角」→
 * 選「重畫背景（恐龍移到旁邊）」→ 做好後有角色動作列。假後端（DemoEngine）產生圖，不需 GPU。
 */
test("套用恐龍包、不放主角開始做 → 第 1 頁沒有動作列 → 勾回放主角並重畫背景 → 有動作列", async ({ page, request }) => {
  test.setTimeout(180_000);
  const book = await createBook(request, uniqueTitle("e2e 不放主角"));
  await setCharacter(request, book.id);

  // 1. 設定頁：套用恐龍包 → 勾「不放主角」
  await page.goto(`/books/${book.id}/setup`);
  await page.getByRole("button", { name: "套用主題包" }).click();
  await page.getByRole("dialog", { name: "套用主題包" }).getByRole("button", { name: /恐龍/ }).click();
  const rows = page.getByRole("list", { name: "每一頁" }).getByRole("listitem");
  await expect(rows.first()).toBeVisible();
  // 總開關在故事框上方（DOM 順序第一個），每一頁還有自己的「不放主角」
  const master = page.getByRole("checkbox", { name: "不放主角", exact: true }).first();
  await master.check();
  await expect(master).toBeChecked();

  // 2. 開始做 → B 整本做好
  const produced = page.waitForResponse((r) => r.url().endsWith(`/api/books/${book.id}/produce`));
  await page.getByRole("button", { name: "開始做這本書" }).click();
  expect((await produced).status()).toBe(202);
  await expect(page).toHaveURL(new RegExp(`/books/${book.id}$`));
  await expect(page.getByRole("region", { name: "整本做好了！" })).toBeVisible({ timeout: 90_000 });

  // 3. 第 1 頁：沒有角色動作列，說明這頁不放主角
  await page.getByRole("list", { name: "頁面" }).getByRole("link").first().click();
  await expect(page.getByRole("heading", { level: 1, name: /^第 1 頁/ })).toBeVisible();
  await expect(page.getByText("這頁不放主角")).toBeVisible();
  await expect(page.getByRole("listitem", { name: "角色動作" })).toHaveCount(0);
  const toggle = page.getByRole("checkbox", { name: "放主角", exact: true });
  await expect(toggle).not.toBeChecked();

  // 4. 勾回「放主角」→ 重畫背景（恐龍移到旁邊）
  const sent = page.waitForResponse((r) => /\/api\/pages\/\d+\/character(\?oneclick=true)?$/.test(r.url()) && r.request().method() === "POST");
  await toggle.click(); // 受控勾選框：先問怎麼處理背景，勾選狀態要等送出後才變
  await page.getByRole("dialog", { name: "把主角加回來" }).getByRole("button", { name: /重畫背景（恐龍移到旁邊）/ }).click();
  expect((await sent).status()).toBeLessThan(300);

  // 5. 做好後：放主角、有角色動作列
  await expect(page.getByRole("listitem", { name: "角色動作" })).toBeVisible({ timeout: 90_000 });
  await expect(toggle).toBeChecked();
  await expect(page.getByText("這頁不放主角")).toHaveCount(0);
});
