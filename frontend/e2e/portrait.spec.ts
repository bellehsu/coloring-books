import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  composePage,
  createBook,
  createPage,
  drainQueueAfterAll,
  framePng,
  getPages,
  setCharacter,
  uniqueTitle,
  uploadBackground,
} from "./seed";

drainQueueAfterAll();

/**
 * 放進框裡（肖像，spec 2026-11-24-portrait-frame-fit §5.5）：進階編輯的舞台。
 * 假後端的背景（上方一個有缺口的框）找不到框 → 說明、按鈕維持沒按；上傳一張有框的背景 → 按下去變肖像：
 * 角色圖層裁進框內、位置／大小快捷鈕停用、說明換成肖像的；自動合成後頁面位置帶 frame／autofit；再按一次回到一般擺放。
 */
const NO_FRAME = "這張背景找不到框，照一般擺放";
const HINT_PORTRAIT = "肖像：主角自動放大，框外的部分裁掉。可以拖曳或拉圓點微調；再按一次『放進框裡』關掉。";

interface Placement {
  cx: number;
  cy: number;
  height: number;
  flip: boolean;
  frame?: { x: number; y: number } | null;
  autofit?: boolean;
}

async function placementOf(request: APIRequestContext, bookId: number, pageId: number): Promise<Placement | null> {
  const p = (await getPages(request, bookId)).find((x) => x.id === pageId) as unknown as { placement: Placement | null };
  return p.placement;
}

test("放進框裡：沒框的背景說明找不到框；有框的背景變肖像（裁切預覽、快捷鈕停用）；再按一次回到一般擺放", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const book = await createBook(request, uniqueTitle("e2e 肖像"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "華麗的相框", "揮手");
  await composePage(request, book.id, p.id);

  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  const character = page.getByRole("group", { name: "角色位置" });
  await expect(character).toHaveAttribute("data-ready", "true");
  const portrait = page.getByRole("button", { name: "放進框裡（肖像）" });
  const portraitStatus = page.getByTestId("portrait-status");
  await expect(portrait).toHaveAttribute("aria-pressed", "false");

  // 假後端的背景沒有完整的框
  await portrait.click();
  await expect(portraitStatus).toHaveText(NO_FRAME);
  await expect(portrait).toHaveAttribute("aria-pressed", "false");
  expect((await placementOf(request, book.id, p.id))?.frame ?? null).toBeNull();

  // 換一張有框的背景（上傳）
  await uploadBackground(request, p.id, framePng(1200, 1600));
  await page.reload();
  await expect(character).toHaveAttribute("data-ready", "true");
  const composeStatus = page.getByRole("status").filter({ hasText: /線稿已自動更新|更新中/ });
  await expect(composeStatus).toHaveText("✓ 線稿已自動更新");

  await portrait.click();
  await expect(portrait).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("cutout-layer")).toHaveAttribute("data-masked", "true");
  await expect(portraitStatus).toBeEmpty();
  for (const name of ["靠左", "置中", "靠右"])
    await expect(page.getByRole("group", { name: "位置快捷鈕" }).getByRole("button", { name })).toBeDisabled();
  for (const name of ["小", "中", "大"])
    await expect(page.getByRole("group", { name: "大小快捷鈕" }).getByRole("button", { name })).toBeDisabled();
  await expect(page.getByText(HINT_PORTRAIT)).toBeVisible();
  await expect(composeStatus).toHaveText("✓ 線稿已自動更新");
  const fitted = await placementOf(request, book.id, p.id);
  expect(fitted).toMatchObject({ frame: { x: 0.5, y: 0.55 }, autofit: true });
  expect(fitted!.height).toBeGreaterThan(0.6);
  await page.screenshot({ path: test.info().outputPath("portrait-on.png") });

  // 再按一次：回到預設位置，frame／autofit 清掉
  await portrait.click();
  await expect(portrait).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("cutout-layer")).not.toHaveAttribute("data-masked", "true");
  // 頁面 API 的 placement 欄位一律帶 frame／autofit（null／false）
  await expect
    .poll(() => placementOf(request, book.id, p.id))
    .toEqual({ cx: 0.5, cy: 0.66, height: 0.6, flip: false, frame: null, autofit: false });
  await expect(composeStatus).toHaveText("✓ 線稿已自動更新");
});
