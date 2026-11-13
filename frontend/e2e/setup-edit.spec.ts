import { expect, test } from "@playwright/test";
import { composePage, createBook, createPage, drainQueue, drainQueueAfterAll, getGenerations, getPages, ok, setCharacter, uniqueTitle } from "./seed";

drainQueueAfterAll();

/** #7：在「主角與故事」改既有頁的動作 → 那一頁標「要重畫」；B 的「繼續做」只重畫那一頁的動作。 */
test("設定頁編輯第 2 頁的動作：要重畫 → 繼續做只多一筆第 2 頁的角色動作，完成後請看一下", async ({ page, request }) => {
  test.setTimeout(150_000);
  const book = await createBook(request, uniqueTitle("e2e 編輯既有頁"));
  await setCharacter(request, book.id);
  const pages = [
    await createPage(request, book.id, "陽光公園", "揮手"),
    await createPage(request, book.id, "安靜的海邊", "坐著看書"),
    await createPage(request, book.id, "熱鬧的市場", "提著籃子"),
  ];
  for (const p of pages) await composePage(request, book.id, p.id);
  await drainQueue(request);

  const perPage = async () => {
    const gens = [...(await getGenerations(request, book.id, "background")), ...(await getGenerations(request, book.id, "pose"))];
    return pages.map((p) => gens.filter((g) => g.page_id === p.id).length);
  };
  const before = await perPage();

  await page.goto(`/books/${book.id}/setup`);
  const row = page.getByRole("listitem", { name: "第 2 頁" });
  await row.getByRole("button", { name: "編輯" }).click();
  const action = row.getByLabel("第 2 頁動作");
  await action.fill("跳起舞來");
  const saved = page.waitForResponse((r) => r.url().includes(`/api/pages/${pages[1].id}`) && r.request().method() === "PATCH");
  await row.getByRole("button", { name: "儲存" }).click();
  expect((await saved).status()).toBe(200);
  await expect(row.getByText("要重畫")).toBeVisible();
  await expect(page.getByRole("listitem", { name: "第 1 頁" }).getByText("要重畫")).toHaveCount(0);

  await page.goto(`/books/${book.id}`);
  await expect(page.getByRole("region", { name: "還有 1 頁要重畫" })).toBeVisible();
  await page.getByRole("button", { name: "繼續做" }).click();

  await expect.poll(async () => (await perPage()).join(","), { timeout: 60_000 }).toBe(
    [before[0], before[1] + 1, before[2]].join(","),
  );
  await drainQueue(request);
  await expect
    .poll(async () => (await getPages(request, book.id)).find((p) => p.id === pages[1].id)?.status, { timeout: 30_000 })
    .toBe("passed");
  const after = await ok<{ id: number; attention: string | null }[]>(await request.get(`/api/books/${book.id}/pages`));
  expect(after.find((p) => p.id === pages[1].id)?.attention).toBe("review_pose");
  expect(await perPage()).toEqual([before[0], before[1] + 1, before[2]]);
});
