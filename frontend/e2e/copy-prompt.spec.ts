import { expect, test } from "@playwright/test";
import { createBook } from "./seed";

test("非安全環境複製不成：跳出全選好的指令視窗", async ({ page, request }) => {
  const book = await createBook(request, `複製指令 ${Date.now()}`);
  // localhost 算安全環境，所以用 init script 模擬 http 網址：沒有 Clipboard API、execCommand 失敗
  await page.addInitScript(() => {
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
    document.execCommand = () => false;
  });
  await page.goto(`/books/${book.id}/setup`);
  await page.getByRole("button", { name: "複製給 AI 的指令" }).click();
  const dialog = page.getByRole("dialog", { name: "複製給 AI 的指令" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("給 AI 的指令")).toHaveValue(/第1頁/);
  await expect(dialog.getByText("已全選，按 Ctrl＋C（手機長按）複製")).toBeVisible();
});
