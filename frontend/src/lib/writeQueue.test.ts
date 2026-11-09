import { describe, expect, it, vi } from "vitest";
import type { Stroke } from "./strokes";
import { createWriteQueue } from "./writeQueue";

/** 可手動決定何時完成的假請求。 */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface FakeQA {
  n: number;
}

/** 模擬後端：最後寫入為準。每次 send 都記下內容，回應時把伺服器狀態設成該次內容。 */
function fakeServer() {
  const calls: { strokes: Stroke[]; d: ReturnType<typeof deferred<FakeQA>> }[] = [];
  let server: Stroke[] = [];
  const send = vi.fn((strokes: Stroke[]) => {
    const d = deferred<FakeQA>();
    calls.push({ strokes, d });
    return d.promise.then((qa) => {
      server = strokes;
      return qa;
    });
  });
  return { send, calls, server: () => server };
}

const st = (i: number): Stroke[] =>
  Array.from({ length: i }, (_, k) => ({ op: "draw", width_px: 11, points: [[k / 10, 0.5]] }));

const settle = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

describe("createWriteQueue", () => {
  it("第一次 push 立刻送出", () => {
    const { send } = fakeServer();
    const q = createWriteQueue(send);
    q.push(st(1));
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenLastCalledWith(st(1));
  });

  it("連推 5 次、第一個請求未完成 → 只會再送 1 次，且內容是第 5 次（Review Focus #1）", async () => {
    const s = fakeServer();
    const q = createWriteQueue(s.send);
    for (let i = 1; i <= 5; i++) q.push(st(i));
    expect(s.send).toHaveBeenCalledTimes(1); // 同時只有一個請求
    expect(s.calls[0].strokes).toEqual(st(1));

    s.calls[0].d.resolve({ n: 1 });
    await settle();
    expect(s.send).toHaveBeenCalledTimes(2);
    expect(s.calls[1].strokes).toEqual(st(5));

    s.calls[1].d.resolve({ n: 5 });
    await settle();
    expect(s.send).toHaveBeenCalledTimes(2);
    expect(s.server()).toEqual(st(5)); // 伺服器＝畫面
  });

  it("前一個失敗後，下一次仍會送；被取代的失敗不回報", async () => {
    const s = fakeServer();
    const onError = vi.fn();
    const onSuccess = vi.fn();
    const q = createWriteQueue(s.send, { onError, onSuccess });
    q.push(st(1));
    q.push(st(2));
    s.calls[0].d.reject(new Error("網路中斷"));
    await settle();
    expect(s.send).toHaveBeenCalledTimes(2);
    expect(s.calls[1].strokes).toEqual(st(2));
    expect(onError).not.toHaveBeenCalled(); // 有更新的狀態在等，失敗那份已不重要

    s.calls[1].d.resolve({ n: 2 });
    await settle();
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onSuccess).toHaveBeenCalledWith({ n: 2 }, st(2));
    expect(s.server()).toEqual(st(2));
  });

  it("最新狀態送失敗 → onError（帶那份筆畫），不自動重試；之後再 push 會再送", async () => {
    const s = fakeServer();
    const onError = vi.fn();
    const q = createWriteQueue(s.send, { onError });
    q.push(st(3));
    const err = new Error("伺服器錯誤（500）");
    s.calls[0].d.reject(err);
    await settle();
    expect(onError).toHaveBeenCalledWith(err, st(3));
    expect(s.send).toHaveBeenCalledTimes(1);

    q.push(st(3)); // 呼叫端決定重試
    expect(s.send).toHaveBeenCalledTimes(2);
    s.calls[1].d.resolve({ n: 3 });
    await settle();
    expect(s.server()).toEqual(st(3));
  });

  it("前一個失敗後的 push：等上一個結束才送（仍然一次一個）", async () => {
    const s = fakeServer();
    const q = createWriteQueue(s.send);
    q.push(st(1));
    q.push(st(2));
    expect(s.send).toHaveBeenCalledTimes(1);
    s.calls[0].d.reject(new Error("x"));
    await settle();
    q.push(st(3));
    expect(s.send).toHaveBeenCalledTimes(2); // st(2) 正在送，st(3) 排隊
    s.calls[1].d.resolve({ n: 2 });
    await settle();
    expect(s.send).toHaveBeenCalledTimes(3);
    expect(s.calls[2].strokes).toEqual(st(3));
  });

  it("onSuccess 只在回應對應最新狀態時呼叫（中間版本的 QA 不套用到畫面）", async () => {
    const s = fakeServer();
    const onSuccess = vi.fn();
    const q = createWriteQueue(s.send, { onSuccess });
    q.push(st(1));
    q.push(st(2));
    s.calls[0].d.resolve({ n: 1 });
    await settle();
    expect(onSuccess).not.toHaveBeenCalled();
    s.calls[1].d.resolve({ n: 2 });
    await settle();
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onSuccess).toHaveBeenCalledWith({ n: 2 }, st(2));
  });

  it("flush：等全部送完，回傳最後一次成功的 QA；閒置時立即回傳", async () => {
    const s = fakeServer();
    const q = createWriteQueue(s.send);
    await expect(q.flush()).resolves.toBeUndefined();
    q.push(st(1));
    q.push(st(2));
    const done = q.flush();
    s.calls[0].d.resolve({ n: 1 });
    await settle();
    s.calls[1].d.resolve({ n: 2 });
    await expect(done).resolves.toEqual({ n: 2 });
    await expect(q.flush()).resolves.toEqual({ n: 2 });
  });

  it("flush：最後一次寫入失敗 → reject 該錯誤（伺服器與畫面不一致）", async () => {
    const s = fakeServer();
    const q = createWriteQueue(s.send);
    q.push(st(1));
    const done = q.flush();
    s.calls[0].d.reject(new Error("壞了"));
    await expect(done).rejects.toThrow("壞了");
  });

  it("send 同步丟錯也當作失敗，不會卡住佇列", async () => {
    let n = 0;
    const send = vi.fn((strokes: Stroke[]) => {
      n++;
      if (n === 1) throw new Error("sync");
      return Promise.resolve({ n: strokes.length });
    });
    const onError = vi.fn();
    const q = createWriteQueue(send, { onError });
    q.push(st(1));
    await settle();
    expect(onError).toHaveBeenCalledTimes(1);
    q.push(st(2));
    await expect(q.flush()).resolves.toEqual({ n: 2 });
  });

  it("onBusyChange：開始送 → true，全部結束 → false（中間不閃爍）", async () => {
    const s = fakeServer();
    const onBusyChange = vi.fn();
    const q = createWriteQueue(s.send, { onBusyChange });
    q.push(st(1));
    q.push(st(2));
    expect(onBusyChange.mock.calls).toEqual([[true]]);
    s.calls[0].d.resolve({ n: 1 });
    await settle();
    expect(onBusyChange.mock.calls).toEqual([[true]]);
    s.calls[1].d.resolve({ n: 2 });
    await settle();
    expect(onBusyChange.mock.calls).toEqual([[true], [false]]);
    expect(q.busy()).toBe(false);
  });

  it("push 會複製陣列：之後呼叫端改動原陣列不影響已排隊的內容", async () => {
    const s = fakeServer();
    const q = createWriteQueue(s.send);
    q.push(st(1));
    const later = st(2);
    q.push(later);
    later.push(...st(1));
    s.calls[0].d.resolve({ n: 1 });
    await settle();
    expect(s.calls[1].strokes).toHaveLength(2);
  });
});
