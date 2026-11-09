import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { qk } from "@/api/hooks";
import type { Stroke } from "@/lib/strokes";
import { editsQueueFor } from "./editsQueue";
import { hasPendingWrites, onBeforeUnload, whenBookWritesIdle } from "./pendingWrites";
import { sketchSaverFor } from "./sketchSaver";

const s = (x: number): Stroke => ({ op: "draw", width_px: 11, points: [[x, x]] });
const tick = () => new Promise((r) => setTimeout(r, 0));
const res = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** 每個請求都等測試放行；放行時指定狀態碼。 */
let gates: { url: string; method: string; release: (status?: number) => void }[];

beforeEach(() => {
  gates = [];
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string, init: RequestInit = {}) =>
        new Promise<Response>((r) =>
          gates.push({
            url,
            method: init.method ?? "GET",
            release: (status = 200) => r(res(status, status === 200 ? {} : { detail: "壞了" })),
          }),
        ),
    ),
  );
});
afterEach(() => vi.unstubAllGlobals());

const releaseAll = async (status = 200) => {
  for (const g of gates.splice(0)) g.release(status);
  await tick();
  await tick();
};

function event() {
  const e = new Event("beforeunload", { cancelable: true }) as BeforeUnloadEvent;
  return e;
}

describe("hasPendingWrites／onBeforeUnload", () => {
  it("什麼都沒有 → false，不攔離開", () => {
    const qc = new QueryClient();
    expect(hasPendingWrites(qc)).toBe(false);
    const e = event();
    onBeforeUnload(qc, e);
    expect(e.defaultPrevented).toBe(false);
  });

  it("草圖存檔送出中 → true，攔下離開；完成後 false", async () => {
    const qc = new QueryClient();
    sketchSaverFor(qc).save(1, 1, new Blob(["x"]));
    expect(hasPendingWrites(qc)).toBe(true);
    const e = event();
    onBeforeUnload(qc, e);
    expect(e.defaultPrevented).toBe(true);
    await releaseAll();
    expect(hasPendingWrites(qc)).toBe(false);
  });

  it("草圖畫布有未存變更（debounce 還沒到）→ true", () => {
    const qc = new QueryClient();
    let dirty = true;
    sketchSaverFor(qc).register(1, 1, { flush: () => {}, dirty: () => dirty });
    expect(hasPendingWrites(qc)).toBe(true);
    dirty = false;
    expect(hasPendingWrites(qc)).toBe(false);
  });

  it("修線佇列忙碌 → true；最後一次寫入失敗 → 仍是 true", async () => {
    const qc = new QueryClient();
    sketchSaverFor(qc); // 先建 saver 也不影響
    const e = editsQueueFor(qc, 1, 10, 7);
    e.push([s(0.1)]);
    expect(hasPendingWrites(qc)).toBe(true);
    await releaseAll(500);
    expect(e.failed).toBe(true);
    expect(hasPendingWrites(qc)).toBe(true);
    e.push([s(0.1)]);
    await releaseAll();
    await releaseAll(); // 成功後的 invalidate 不算
    expect(hasPendingWrites(qc)).toBe(false);
  });

  it("草圖存檔失敗 → true", async () => {
    const qc = new QueryClient();
    sketchSaverFor(qc).save(1, 1, new Blob(["x"]));
    await releaseAll(500);
    expect(hasPendingWrites(qc)).toBe(true);
  });
});

describe("whenBookWritesIdle", () => {
  it("等這本書的修線佇列與草圖都寫完", async () => {
    const qc = new QueryClient();
    editsQueueFor(qc, 1, 10, 7).push([s(0.1)]);
    sketchSaverFor(qc).save(1, 11, new Blob(["x"]));
    editsQueueFor(qc, 2, 20, 8).push([s(0.2)]); // 別本書：不等
    let done = false;
    const w = whenBookWritesIdle(qc, 1).then(() => (done = true));
    await tick();
    expect(done).toBe(false);
    for (const g of gates.splice(0)) if (!g.url.includes("/pages/20/")) g.release();
    await w;
    expect(done).toBe(true);
  });

  it("任一寫入失敗 → reject", async () => {
    const qc = new QueryClient();
    editsQueueFor(qc, 1, 10, 7).push([s(0.1)]);
    const w = whenBookWritesIdle(qc, 1).then(
      () => null,
      (e: unknown) => e,
    );
    await releaseAll(500);
    expect(await w).toMatchObject({ message: "壞了" });
  });

  it("已不是選定圖的修線佇列（失敗後換了選圖）不擋匯出：PDF 只用選定圖", async () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.pages(1), [{ id: 10, selected_generation_id: 8 }]);
    editsQueueFor(qc, 1, 10, 7).push([s(0.1)]); // 舊選圖 7
    await releaseAll(500);
    await expect(whenBookWritesIdle(qc, 1)).resolves.toBeUndefined();
    editsQueueFor(qc, 1, 10, 8).push([s(0.2)]); // 目前選圖 8
    const w = whenBookWritesIdle(qc, 1).then(
      () => null,
      (e: unknown) => e,
    );
    await releaseAll(500);
    expect(await w).toMatchObject({ message: "壞了" });
  });
});
