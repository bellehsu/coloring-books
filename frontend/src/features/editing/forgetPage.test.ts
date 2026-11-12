import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { editsQueueFor, peekEditsQueue } from "./editsQueue";
import { forgetDeletedPage, forgetPageId } from "./forgetPage";
import { placementSaverFor } from "./placementSaver";

const ok = () => new Response(JSON.stringify({ id: 5, book_id: 1 }), { status: 200, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
const at = (cx: number) => ({ cx, cy: 0.6, height: 0.5, flip: false });

beforeEach(() => {
  fetchMock = vi.fn(async () => ok());
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

/** 頁 5 留下位置與修線佇列；接著刪除成功。 */
async function deletedPage5(qc: QueryClient) {
  const s = placementSaverFor(qc);
  s.savePlacement(5, at(0.3));
  await s.whenIdle(5);
  editsQueueFor(qc, 1, 5, 7);
  forgetDeletedPage(qc, 5);
  return s;
}

describe("forgetDeletedPage / forgetPageId", () => {
  it("刪除成功：清掉修線佇列；位置記成已刪除（之後的存檔不送）", async () => {
    const qc = new QueryClient();
    const s = await deletedPage5(qc);
    expect(peekEditsQueue(qc, 5, 7)).toBeUndefined();
    expect(s.isGone(5)).toBe(true);
    expect(s.latest(5)).toBeUndefined();
    s.savePlacement(5, at(0.4));
    expect(fetchMock).toHaveBeenCalledTimes(1); // 只有刪除前那一筆
  });

  it("id 被新頁沿用：已刪除標記也清掉，存檔照常送出", async () => {
    const qc = new QueryClient();
    const s = await deletedPage5(qc);
    forgetPageId(qc, 5);
    expect(s.isGone(5)).toBe(false);
    s.savePlacement(5, at(0.4));
    expect(s.latest(5)).toEqual(at(0.4));
    await s.whenIdle(5);
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/placement"))).toHaveLength(2);
    expect(peekEditsQueue(qc, 5, 7)).toBeUndefined();
  });
});
