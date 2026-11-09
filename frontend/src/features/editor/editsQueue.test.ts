import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { qk } from "@/api/hooks";
import type { Stroke } from "@/lib/strokes";
import { editsQueueFor } from "./editsQueue";

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
});
