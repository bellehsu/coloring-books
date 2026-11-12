import { expect, test, type Page } from "@playwright/test";
import { createBook, uniqueTitle } from "./seed";

/**
 * 按快捷鍵叫出（或關閉）說明。快捷鍵的監聽器要等 React hydrate 才掛上，頁面標題在 SSR 就看得到，
 * 不代表可以按了；太早按會被吞掉。所以沒反應就再按一次（按下後 2 秒內沒變成預期狀態才重按），
 * 不依賴固定等待，也不會因為重按而把已經開好的說明關掉（每次重按前先確認目前狀態）。
 */
async function toggleHelp(page: Page, key: string, want: "open" | "closed"): Promise<void> {
  const help = page.getByRole("dialog", { name: "現在的畫圖流程" });
  await expect(async () => {
    const isOpen = await help.isVisible();
    if ((want === "open") !== isOpen) await page.keyboard.press(key);
    if (want === "open") await expect(help).toBeVisible({ timeout: 2_000 });
    else await expect(help).toBeHidden({ timeout: 2_000 });
  }).toPass({ timeout: 20_000 });
}

/** 任何頁面按 h／H 叫出「現在的畫圖流程」說明；再按 h 或 Esc 關閉；在輸入框裡打 h 不會觸發。 */
test("首頁按 h 叫出說明、h 關閉；設定頁按 H 叫出、Esc 關閉；書名欄位打 h 不觸發", async ({ page, request }, testInfo) => {
  const book = await createBook(request, uniqueTitle("e2e 說明"));

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  const help = page.getByRole("dialog", { name: "現在的畫圖流程" });
  await toggleHelp(page, "h", "open");
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
  await toggleHelp(page, "Shift+H", "open");
  await page.keyboard.press("Escape");
  await expect(help).toBeHidden();

  await title.click();
  await page.keyboard.press("End");
  await page.keyboard.type("h");
  await expect(help).toBeHidden();
  await expect(title).toHaveValue(/h$/);
});
