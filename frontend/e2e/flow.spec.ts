import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { createBook, createPage, setCharacter, uniqueTitle } from "./seed";

const POSES = /\/api\/pages\/\d+\/poses$/;

/** 角色動作歷史裡 seed 的那張卡（成功後才有「選 seed N 為本頁角色動作」按鈕）。 */
const poseCard = (page: Page, seed: number) =>
  page
    .getByRole("region", { name: "角色動作歷史" })
    .getByRole("listitem")
    .filter({ has: page.getByRole("button", { name: `選 seed ${seed} 為本頁角色動作`, exact: true }) });

/** 按「產生角色動作」：送出的 mode 要是 expected；回傳新 generation 的 seed。 */
async function generatePose(page: Page, expected: "precise" | "fast"): Promise<number> {
  const sent = page.waitForRequest((r) => POSES.test(r.url()) && r.method() === "POST");
  const answered = page.waitForResponse((r) => POSES.test(r.url()) && r.request().method() === "POST");
  await page.getByRole("button", { name: "產生角色動作" }).click();
  expect((await sent).postDataJSON()).toEqual({ action: "waving one hand", mode: expected });
  const res = await answered;
  expect(res.status()).toBe(202);
  const gen = (await res.json()) as { seed: number; pose_mode: string };
  expect(gen.pose_mode).toBe(expected);
  return gen.seed;
}

test("完整流程：建書 → 角色 → 背景 → 角色動作 → 合成 → 修線 → QA 通過 → 匯出 PDF", async ({ page }) => {
  const title = uniqueTitle("e2e 小貓");

  // ---- 我的繪本：建立 A4 印刷廠的書 ----
  await page.goto("/");
  await page.getByRole("button", { name: "建立新繪本" }).first().click();
  const dialog = page.getByRole("dialog", { name: "建立新繪本" });
  await dialog.getByLabel("書名").fill(title);
  await dialog.getByRole("radio", { name: /A4/ }).click();
  await dialog.getByRole("radio", { name: /印刷廠/ }).click();
  await dialog.getByRole("button", { name: /建立，接著設定角色/ }).click();

  // ---- 角色工作室：產生 4 張 → 選一張 → 設為本書角色 ----
  await expect(page).toHaveURL(/\/books\/\d+\/character$/);
  await page.getByLabel("描述角色的外型").fill("a round cat with big ears");
  await page.getByRole("button", { name: "產生 4 張" }).click();
  const charCards = page.getByRole("button", { name: /^選 seed \d+$/ });
  await expect(charCards).toHaveCount(4);
  await charCards.nth(1).click();
  await expect(charCards.nth(1)).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "設為本書角色，開始做頁面" }).click();

  // ---- 頁面編輯器：新增頁 → 背景（畫方框 → 產生背景 → 選用）----
  await expect(page).toHaveURL(/\/books\/\d+\/editor/);
  await page.getByRole("button", { name: "新增頁面" }).click();
  // 分頁名稱開頭比對：QA 分頁有缺口時名稱後面會接「1 個缺口」
  const tab = (name: string) => page.getByRole("tab", { name: new RegExp(`^${name}`) });
  await expect(tab("背景")).toHaveAttribute("aria-selected", "true");
  for (const name of ["合成", "線稿與修線", "QA 檢查"]) await expect(tab(name)).toHaveAttribute("aria-disabled", "true");
  const canvas = page.getByRole("img", { name: /構圖畫板/ });
  await expect(canvas).toBeVisible();
  await page.getByLabel("場景描述").fill("in a sunny park");
  await page.getByRole("button", { name: "方框" }).click();
  const box = (await canvas.boundingBox())!;
  // 記錄順序：草圖 PUT 完成、背景 POST 送出。畫完立刻按產生（不等 1 秒 debounce），
  // 產生必須先把草圖存好，後端在 POST 當下讀 sketch_path。
  const order: string[] = [];
  page.on("requestfinished", (r) => {
    if (/\/api\/pages\/\d+\/sketch$/.test(r.url()) && r.method() === "PUT") order.push("sketch saved");
  });
  page.on("request", (r) => {
    if (/\/api\/pages\/\d+\/backgrounds$/.test(r.url()) && r.method() === "POST") order.push("background sent");
  });
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.6);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.75, { steps: 5 });
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.9, { steps: 5 });
  await page.mouse.up();
  await page.getByRole("button", { name: "產生背景" }).click();
  const bgCards = page.getByRole("button", { name: /^選 seed \d+ 為本頁背景$/ });
  await expect(bgCards).toHaveCount(1);
  expect(order).toEqual(["sketch saved", "background sent"]);
  await bgCards.first().click();
  await expect(bgCards.first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: "背景歷史" }).getByText("使用中")).toBeVisible();
  await expect(page.getByRole("img", { name: "背景圖" })).toBeVisible();

  // ---- 角色：點「揮手」→ 預設「精準」產生一張 → 切到「快速」再產生一張 → 選用精準那張（去背預覽）----
  await tab("角色").click();
  await page.getByRole("button", { name: "揮手" }).click();
  await expect(page.getByLabel("動作描述")).toHaveValue("waving one hand");
  await expect(page.getByRole("button", { name: "揮手（已加入）" })).toBeDisabled();
  const poseModes = page.getByRole("radiogroup", { name: "產生方式" });
  const precise = poseModes.getByRole("radio", { name: "精準" });
  const fast = poseModes.getByRole("radio", { name: "快速" });
  await expect(precise).toHaveAttribute("aria-checked", "true");
  await expect(precise).toBeEnabled();
  await expect(fast).toHaveAttribute("aria-checked", "false");
  await expect(poseModes).toHaveAccessibleDescription("約 30 秒，角色最像、動作最準");
  const preciseSeed = await generatePose(page, "precise");
  await fast.click();
  await expect(fast).toHaveAttribute("aria-checked", "true");
  await expect(poseModes).toHaveAccessibleDescription("約 10 秒，可能和角色有落差");
  expect(await page.evaluate(() => localStorage.getItem("coloring.poseMode"))).toBe("fast");
  const fastSeed = await generatePose(page, "fast");
  const poseCards = page.getByRole("button", { name: /^選 seed \d+ 為本頁角色動作$/ });
  await expect(poseCards).toHaveCount(2);
  await expect(poseCard(page, preciseSeed).getByText("精準", { exact: true })).toBeVisible();
  await expect(poseCard(page, fastSeed).getByText("快速", { exact: true })).toBeVisible();
  const pickPrecise = poseCard(page, preciseSeed).getByRole("button", { name: /^選 seed \d+ 為本頁角色動作$/ });
  await pickPrecise.click();
  await expect(pickPrecise).toHaveAttribute("aria-pressed", "true");
  await expect(poseCard(page, preciseSeed).getByText("使用中")).toBeVisible();
  await expect(page.getByRole("img", { name: "角色動作（去背預覽）" })).toBeVisible();

  // ---- 合成：拖曳＋方向鍵移動角色 → 立刻確認合成（位置先寫完才合成）----
  await expect(tab("線稿與修線")).toHaveAttribute("aria-disabled", "true");
  await tab("合成").click();
  const character = page.getByRole("group", { name: "角色位置" });
  await expect(character).toHaveAttribute("data-ready", "true");
  const readout = page.getByText(/^水平 \d+%・垂直 \d+%・高度 \d+%$/);
  await expect(readout).toHaveText("水平 50%・垂直 66%・高度 60%");
  // 滑鼠拖曳：往下 2%（舞台高）
  const overlay = (await page.getByTestId("compose-overlay").boundingBox())!;
  const start = (await character.boundingBox())!;
  const [sx, sy] = [start.x + start.width / 2, start.y + start.height / 2];
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx, sy + overlay.height * 0.01, { steps: 3 });
  await page.mouse.move(sx, sy + overlay.height * 0.02, { steps: 3 });
  await page.mouse.up();
  await expect(readout).toHaveText("水平 50%・垂直 68%・高度 60%");
  // 位置 PUT 故意變慢：確認合成必須等最後一次位置寫完才送出（合成結果＝畫面上的位置）
  const events: string[] = [];
  await page.route(/\/api\/pages\/\d+\/placement$/, async (route) => {
    await new Promise((r) => setTimeout(r, 400));
    await route.continue();
  });
  page.on("requestfinished", (r) => {
    if (/\/api\/pages\/\d+\/placement$/.test(r.url()) && r.method() === "PUT")
      events.push(`placement ${(r.postDataJSON() as { cx: number }).cx.toFixed(2)}`);
  });
  page.on("request", (r) => {
    if (/\/api\/pages\/\d+\/compose$/.test(r.url()) && r.method() === "POST") events.push("compose sent");
  });
  await character.focus();
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowLeft");
  await expect(readout).toHaveText("水平 47%・垂直 68%・高度 60%");
  const composing = page.waitForResponse((r) => /\/api\/pages\/\d+\/compose$/.test(r.url()));
  await page.getByRole("button", { name: "確認合成" }).click();
  const composed = await composing;
  expect(composed.status()).toBe(200);
  expect(events.at(-1)).toBe("compose sent");
  expect(events.at(-2)).toBe("placement 0.47");
  // 合成當下頁面的位置（後端用它合成）＝畫面上的位置，且已有選定的合成圖
  const composedPage = (await composed.json()) as {
    placement: { cx: number; cy: number; height: number; flip: boolean };
    selected_generation_id: number | null;
  };
  expect(composedPage.placement).toMatchObject({ cx: 0.47, height: 0.6, flip: false });
  expect(composedPage.placement.cy).toBeCloseTo(0.68, 2);
  expect(composedPage.selected_generation_id).not.toBeNull();
  await page.unroute(/\/api\/pages\/\d+\/placement$/);

  // ---- 確認後自動切到線稿與修線：看到紅圈 → 自動補線 → 缺口 0 ----
  await expect(tab("線稿與修線")).toHaveAttribute("aria-selected", "true");
  const lineStatus = page.getByRole("status").filter({ hasText: /缺口/ });
  await expect(lineStatus).toHaveText(/1 個缺口會讓塗色漏出去（紅圈）/);
  await expect(page.locator('[data-part="gap"]')).toHaveCount(1);
  await expect(tab("QA 檢查")).toContainText("1");
  await page.getByRole("toolbar", { name: "修線工具" }).getByRole("button", { name: "自動補線" }).click();
  await expect(page.getByText("已補 1 條")).toBeVisible();
  await expect(page.getByText("沒有缺口")).toBeVisible();
  await expect(page.locator('[data-part="gap"]')).toHaveCount(0);

  // ---- QA：角色一致 → 標為通過 ----
  await tab("QA 檢查").click();
  const qa = page.getByTestId("qa-panel");
  await expect(qa.getByText("線條都有封閉")).toBeVisible();
  await qa.getByRole("radio", { name: "一致", exact: true }).click();
  await expect(qa.getByRole("radio", { name: "一致", exact: true })).toHaveAttribute("aria-checked", "true");
  await qa.getByRole("button", { name: "標為通過" }).click();
  await expect(qa.getByRole("button", { name: /已通過/ })).toBeVisible();

  // ---- 匯出抽屜：下載 PDF ----
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  await expect(drawer.getByTestId("passed-count")).toHaveText("1");
  const downloading = page.waitForEvent("download");
  await drawer.getByRole("button", { name: "下載 PDF（1 頁）" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const bytes = await readFile((await download.path())!);
  expect(bytes.subarray(0, 4).toString("latin1")).toBe("%PDF");
  await expect(drawer.getByRole("status")).toContainText("已產生");
});

test("沒設角色：不能產生角色動作，後端的 409 訊息會顯示在畫面上", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 沒有角色"));
  const p = await createPage(request, book.id, "in a garden");

  // 介面先擋：角色分頁停用（網址指定也退回背景）並提示
  await page.goto(`/books/${book.id}/editor?page=${p.id}&mode=pose`);
  await expect(page.getByText("這本繪本還沒設定角色，不能產生角色動作。")).toBeVisible();
  await expect(page.getByRole("tab", { name: "角色", exact: true })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByRole("tab", { name: "背景", exact: true })).toHaveAttribute("aria-selected", "true");

  // 前端資料過期（例如另一個分頁的狀態）時仍可能送出：讓畫面以為已有角色，後端的 409 中文訊息要原樣顯示
  await page.route(`**/api/books/${book.id}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const json = await res.json();
    await route.fulfill({ response: res, json: { ...json, character_generation_id: 999_999 } });
  });
  await page.reload();
  await expect(page.getByRole("tab", { name: "角色", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "揮手" }).click();
  const generate = page.getByRole("button", { name: "產生角色動作" });
  await expect(generate).toBeEnabled();
  const rejected = page.waitForResponse((r) => r.url().endsWith(`/api/pages/${p.id}/poses`));
  await generate.click();
  expect((await rejected).status()).toBe(409);
  await expect(page.getByRole("alert").filter({ hasText: "請先設定角色" })).toBeVisible();
});

test("精準模式不可用：「精準」停用並顯示缺少什麼，改用「快速」產生", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 精準不可用"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "in a park");
  // 後端快取的 preflight（假後端不跑 preflight → 兩種都可用）：在瀏覽器這端改成精準不可用
  const missing = ["qwen-image-edit-2511-Q4_K_M.gguf", "UnetLoaderGGUF"];
  await page.route("**/api/system/pose-modes", (route) =>
    route.fulfill({
      json: { precise: { available: false, missing }, fast: { available: true, missing: [] } },
    }),
  );
  await page.goto(`/books/${book.id}/editor?page=${p.id}&mode=pose`);
  await expect(page.getByRole("tab", { name: "角色", exact: true })).toHaveAttribute("aria-selected", "true");

  const poseModes = page.getByRole("radiogroup", { name: "產生方式" });
  const precise = poseModes.getByRole("radio", { name: "精準" });
  const fast = poseModes.getByRole("radio", { name: "快速" });
  const reason = `精準模式缺少：${missing.join("、")}`;
  await expect(precise).toBeDisabled();
  await expect(precise).toHaveAttribute("aria-checked", "false");
  await expect(fast).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(reason)).toBeVisible();
  await expect(poseModes).toHaveAccessibleDescription(`約 10 秒，可能和角色有落差 ${reason}`);
  // 改用快速只是畫面上的退路：不改寫記住的選擇（精準恢復後會回到精準）
  expect(await page.evaluate(() => localStorage.getItem("coloring.poseMode"))).toBeNull();

  await page.getByRole("button", { name: "揮手" }).click();
  const seed = await generatePose(page, "fast");
  await expect(poseCard(page, seed).getByText("快速", { exact: true })).toBeVisible();
});
