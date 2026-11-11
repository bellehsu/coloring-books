import { expect, test } from "@playwright/test";
import { createBook, uniqueTitle } from "./seed";

/** 任何頁面按 h／H 叫出「現在的畫圖流程」說明；再按 h 或 Esc 關閉；在輸入框裡打 h 不會觸發。 */
test("首頁按 h 叫出說明、h 關閉；設定頁按 H 叫出、Esc 關閉；書名欄位打 h 不觸發", async ({ page, request }, testInfo) => {
  const book = await createBook(request, uniqueTitle("e2e 說明"));

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  const help = page.getByRole("dialog", { name: "現在的畫圖流程" });
  await page.keyboard.press("h");
  await expect(help).toBeVisible();
  await expect(help.getByRole("heading", { name: /③ 變成著色頁/ })).toBeVisible();
  await expect(help).toContainText("qwen_edit_pose_cutout");
  await page.setViewportSize({ width: 1440, height: 1400 });
  await help.screenshot({ path: testInfo.outputPath("help-desktop.png"), animations: "disabled" });
  // 內容不比視窗寬（沒有水平捲動）
  const overflow = await help.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.keyboard.press("h");
  await expect(help).toBeHidden();

  await page.goto(`/books/${book.id}/setup`);
  const title = page.getByRole("textbox").first();
  await expect(title).toBeVisible();
  await page.keyboard.press("Shift+H");
  await expect(help).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(help).toBeHidden();

  await title.click();
  await page.keyboard.press("End");
  await page.keyboard.type("h");
  await expect(help).toBeHidden();
  await expect(title).toHaveValue(/h$/);
});
