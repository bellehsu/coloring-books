import { expect, test } from "@playwright/test";
import { createBook, createPage, setCharacter, uniqueTitle, uploadSketch } from "./seed";

test("產生全部素材：有場景的頁排背景、有動作的頁排角色動作，上傳頁略過背景", async ({ request }) => {
  const book = await createBook(request, uniqueTitle("e2e 產生全部素材"));
  await setCharacter(request, book.id);
  const p1 = await createPage(request, book.id, "in a sunny park");
  const p2 = await createPage(request, book.id, "on a quiet beach");
  const p3 = await createPage(request, book.id, "");
  await uploadSketch(request, p2.id); // 構圖草圖頁：後端改用細緻
  for (const p of [p1, p2]) {
    const res = await request.patch(`/api/pages/${p.id}`, { data: { action_prompt: "揮手" } });
    expect(res.ok()).toBeTruthy();
  }

  const res = await request.post(`/api/books/${book.id}/materials/batch`, { data: { upload_page_ids: [p2.id] } });
  expect(res.status()).toBe(202);
  const body = (await res.json()) as {
    queued: { page_id: number; kind: string }[];
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
});
