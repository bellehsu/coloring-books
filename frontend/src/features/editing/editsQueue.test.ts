import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { qk } from "@/api/hooks";
import type { Stroke } from "@/lib/strokes";
import { editsQueueFor, flushAllEditsFor, forgetEditsQueues, peekEditsQueue, pruneEditsQueues } from "./editsQueue";

const s = (x: number): Stroke => ({ op: "draw", width_px: 11, points: [[x, x]] });
const tick = () => new Promise((r) => setTimeout(r, 0));

let resolvers: ((qa: unknown) => void)[];
let bodies: unknown[];

beforeEach(() => {
  resolvers = [];
  bodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      if (init.method === "PUT") {
        bodies.push(JSON.parse(init.body as string));
        const qa = await new Promise((r) => resolvers.push(r));
        return new Response(JSON.stringify(qa), { status: 200, headers: { "content-type": "application/json" } });
      }
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("editsQueueFor", () => {
  it("同一個 QueryClient＋頁＋選圖回傳同一個 entry（重新掛載也共用，不會兩個請求並行）", () => {
    const qc = new QueryClient();
    const a = editsQueueFor(qc, 1, 10, 7);
    expect(editsQueueFor(qc, 1, 10, 7)).toBe(a);
    expect(editsQueueFor(qc, 1, 10, 8)).not.toBe(a);
    expect(editsQueueFor(new QueryClient(), 1, 10, 7)).not.toBe(a);
  });

  it("push 依序送出、帶 generation_id；成功後寫入 QA 快取並更新 edits 快取", async () => {
    const qc = new QueryClient();
    const e = editsQueueFor(qc, 1, 10, 7);
    const busy: boolean[] = [];
    const off = e.subscribe(() => busy.push(e.busy()));
    e.push([s(0.1)]);
    e.push([s(0.1), s(0.2)]);
    e.push([s(0.1), s(0.2), s(0.3)]);
    await tick();
    expect(bodies).toHaveLength(1);
    expect(e.busy()).toBe(true);
    expect(e.latest).toHaveLength(3);
    expect(qc.getQueryData(qk.edits(10))).toEqual({ generation_id: 7, strokes: e.latest });
    resolvers.shift()!({ gap_count: 9 });
    await tick();
    await tick();
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toEqual({ generation_id: 7, strokes: [s(0.1), s(0.2), s(0.3)] });
    expect(qc.getQueryData(qk.qa(10))).toBeUndefined(); // 中間結果不套用
    resolvers.shift()!({ gap_count: 0 });
    await tick();
    await tick();
    expect(qc.getQueryData(qk.qa(10))).toEqual({ gap_count: 0 });
    expect(e.busy()).toBe(false);
    expect(busy).toEqual([true, false]);
    off();
  });

  it("寫入失敗 → onError 收到錯誤；failed 為 true 直到下一次 push", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "選圖已更換" }), { status: 409 })),
    );
    const qc = new QueryClient();
    const e = editsQueueFor(qc, 1, 11, 7);
    const onError = vi.fn();
    e.setErrorHandler(onError);
    e.push([s(0.5)]);
    await tick();
    await tick();
    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0][0] as Error).message).toBe("選圖已更換");
    expect(e.failed).toBe(true);
    expect(e.shouldSeedFromLocal()).toBe(true);
  });

  it("閒置且沒失敗 → 從伺服器 seed", () => {
    const e = editsQueueFor(new QueryClient(), 1, 12, 7);
    expect(e.shouldSeedFromLocal()).toBe(false);
  });

  it("push 時取消進行中的 edits GET：晚到的舊回應不會蓋掉樂觀資料；成功後寫入確認的筆畫", async () => {
    let releaseGet!: () => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit = {}) => {
        if (init.method === "PUT") {
          bodies.push(JSON.parse(init.body as string));
          const qa = await new Promise((r) => resolvers.push(r));
          return new Response(JSON.stringify(qa), { status: 200 });
        }
        await new Promise<void>((r) => (releaseGet = r));
        return new Response(JSON.stringify({ generation_id: 7, strokes: [] }), { status: 200 });
      }),
    );
    const qc = new QueryClient();
    const get = qc
      .fetchQuery({
        queryKey: qk.edits(20),
        queryFn: async () => (await fetch("/api/pages/20/lineart/edits")).json(),
      })
      .catch(() => undefined);
    await tick();
    const e = editsQueueFor(qc, 1, 20, 7);
    e.push([s(0.1)]);
    releaseGet();
    await get;
    await tick();
    expect(qc.getQueryData(qk.edits(20))).toEqual({ generation_id: 7, strokes: [s(0.1)] });
    // 伺服器確認後（期間被其他東西改壞了快取）→ 寫回確認的筆畫
    qc.setQueryData(qk.edits(20), { generation_id: 7, strokes: [] });
    resolvers.shift()!({ gap_count: 0 });
    await tick();
    await tick();
    expect(qc.getQueryData(qk.edits(20))).toEqual({ generation_id: 7, strokes: [s(0.1)] });
  });

  it("prune：閒置、沒失敗、不再是選定圖的 entry 被移除；忙碌或失敗的保留；forget 移除整頁", async () => {
    const qc = new QueryClient();
    const idleOld = editsQueueFor(qc, 1, 30, 1);
    const busyOld = editsQueueFor(qc, 1, 30, 2);
    const current = editsQueueFor(qc, 1, 30, 3);
    busyOld.push([s(0.2)]);
    pruneEditsQueues(qc, 30, 3);
    expect(peekEditsQueue(qc, 30, 1)).toBeUndefined();
    expect(peekEditsQueue(qc, 30, 2)).toBe(busyOld);
    expect(peekEditsQueue(qc, 30, 3)).toBe(current);
    expect(editsQueueFor(qc, 1, 30, 1)).not.toBe(idleOld);
    forgetEditsQueues(qc, 30);
    expect(peekEditsQueue(qc, 30, 2)).toBeUndefined();
    expect(peekEditsQueue(qc, 30, 3)).toBeUndefined();
  });

  it("flushAllEditsFor：同一頁同一張圖的兩個佇列（進階編輯與 C）都送完才 resolve；都沒有就馬上 resolve", async () => {
    const qc = new QueryClient();
    await expect(flushAllEditsFor(qc, 40, 7)).resolves.toBeUndefined();
    const editor = editsQueueFor(qc, 1, 40, 7);
    const c = editsQueueFor(qc, 1, 40, 7, { oneclick: true });
    expect(c).not.toBe(editor);
    editor.push([s(0.1)]);
    c.push([s(0.2)]);
    let done = false;
    void flushAllEditsFor(qc, 40, 7).then(() => (done = true));
    await tick();
    expect(bodies).toHaveLength(2);
    resolvers.shift()!({ gap_count: 0 }); // 進階編輯的先回來：C 的還在路上
    await tick();
    await tick();
    expect(editor.busy()).toBe(false);
    expect(done).toBe(false);
    resolvers.shift()!({ gap_count: 0 });
    await tick();
    await tick();
    expect(done).toBe(true);
  });

  it("flushAllEditsFor：另一邊的寫入失敗 → reject（等兩個都結束才回報）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.includes("oneclick")
          ? new Response(JSON.stringify({ detail: "選圖已更換" }), { status: 409 })
          : new Response(JSON.stringify({ gap_count: 0 }), { status: 200 }),
      ),
    );
    const qc = new QueryClient();
    editsQueueFor(qc, 1, 41, 7).push([s(0.1)]);
    editsQueueFor(qc, 1, 41, 7, { oneclick: true }).push([s(0.2)]);
    await expect(flushAllEditsFor(qc, 41, 7)).rejects.toThrow("選圖已更換");
  });
});
