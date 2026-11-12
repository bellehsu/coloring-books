import { expect, test } from "@playwright/test";
import { createBook, createPage, getPages, setCharacter, uniqueTitle, drainQueueAfterAll } from "./seed";

drainQueueAfterAll();

test("產生全部素材：有場景的頁排背景、有動作的頁排角色動作，上傳頁略過背景；完成後自動選用", async ({ request }) => {
  const book = await createBook(request, uniqueTitle("e2e 產生全部素材"));
  await setCharacter(request, book.id);
  const p1 = await createPage(request, book.id, "陽光公園，有一棵大樹", "開心地揮手");
  const p2 = await createPage(request, book.id, "安靜的海邊", "坐著看書");
  const p3 = await createPage(request, book.id);

  // p2 在故事頁設成「上傳圖片」：場景有字也不排背景
  const res = await request.post(`/api/books/${book.id}/materials/batch`, { data: { upload_page_ids: [p2.id] } });
  expect(res.status()).toBe(202);
  const body = (await res.json()) as {
    queued: { id: number; page_id: number; kind: string }[];
    skipped: { page_id: number; kind: string; reason: string }[];
  };
  expect(body.queued.map((g) => [g.page_id, g.kind])).toEqual([
    [p1.id, "background"],
    [p1.id, "pose"],
    [p2.id, "pose"],
  ]);
  expect(body.skipped).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ page_id: p2.id, kind: "background", reason: "改用上傳圖片" }),
      expect.objectContaining({ page_id: p3.id, kind: "background", reason: "沒有場景描述" }),
    ]),
  );

  // worker 完成後自動選用（不用再按）：p1 兩個欄位、p2 只有角色動作、p3 都沒有
  const ids = Object.fromEntries(body.queued.map((g) => [`${g.page_id}:${g.kind}`, g.id]));
  await expect
    .poll(async () =>
      (await getPages(request, book.id)).map((p) => [p.id, p.background_generation_id, p.pose_generation_id]),
    )
    .toEqual([
      [p1.id, ids[`${p1.id}:background`], ids[`${p1.id}:pose`]],
      [p2.id, null, ids[`${p2.id}:pose`]],
      [p3.id, null, null],
    ]);

  // 再按一次：都已經有了 → 不再排
  const again = (await (
    await request.post(`/api/books/${book.id}/materials/batch`, { data: { upload_page_ids: [p2.id] } })
  ).json()) as { queued: unknown[] };
  expect(again.queued).toEqual([]);
});
