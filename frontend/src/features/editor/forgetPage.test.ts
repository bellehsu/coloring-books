import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { editsQueueFor, peekEditsQueue } from "./editsQueue";
import { forgetDeletedPage, forgetPageId } from "./forgetPage";
import { sendTracked, sentFor } from "./sentFields";
import { sketchSaverFor } from "./sketchSaver";

const ok = () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => ok());
  vi.stubGlobal("fetch", fetchMock);
  URL.createObjectURL = vi.fn(() => "blob:1");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

/** 頁 5 留下草圖、場景描述、修線佇列；接著刪除成功。 */
async function deletedPage5(qc: QueryClient) {
  const s = sketchSaverFor(qc);
  s.save(1, 5, new Blob(["old"]));
  await s.whenIdle(5);
  await sendTracked(qc, 5, "scene", "old scene", async () => {});
  editsQueueFor(qc, 1, 5, 7);
  s.beginDelete(5);
  s.save(1, 5, new Blob(["parked"]));
  forgetDeletedPage(qc, 5);
  return s;
}

describe("forgetDeletedPage / forgetPageId", () => {
  it("刪除成功：清掉場景描述與修線佇列；草圖記成已刪除（之後的 save 不送）", async () => {
    const qc = new QueryClient();
    const s = await deletedPage5(qc);
    expect(sentFor(qc, 5).scene).toBeUndefined();
    expect(peekEditsQueue(qc, 5, 7)).toBeUndefined();
    expect(s.isGone(5)).toBe(true);
    expect(s.latest(5)).toBeUndefined();
    s.save(1, 5, new Blob(["x"]));
    expect(fetchMock).toHaveBeenCalledTimes(1); // 只有刪除前那一筆；停著的與之後的都不送
  });

  it("id 被新頁沿用：草圖的已刪除標記也清掉，save 照常送出", async () => {
    const qc = new QueryClient();
    const s = await deletedPage5(qc);
    forgetPageId(qc, 5);
    expect(s.isGone(5)).toBe(false);
    const b = new Blob(["new"]);
    s.save(1, 5, b);
    expect(s.latest(5)).toBe(b);
    await s.whenIdle(5);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentFor(qc, 5).scene).toBeUndefined();
    expect(peekEditsQueue(qc, 5, 7)).toBeUndefined();
  });

  it("id 被沿用時連刪除中停著的存檔一起丟掉（不會送到新頁）", async () => {
    const qc = new QueryClient();
    const s = sketchSaverFor(qc);
    s.beginDelete(5);
    s.save(1, 5, new Blob(["parked"]));
    forgetPageId(qc, 5);
    s.endDelete(5, false); // 舊的刪除流程收尾：已沒有停著的存檔
    expect(fetchMock).not.toHaveBeenCalled();
    expect(s.isGone(5)).toBe(false);
  });
});
