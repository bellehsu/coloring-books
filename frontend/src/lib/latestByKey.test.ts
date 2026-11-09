import { describe, expect, it, vi } from "vitest";
import { createLatestByKey } from "./latestByKey";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("createLatestByKey", () => {
  it("同一個 key 一次只送一個；送出中再 push 只保留最新", async () => {
    const pending: { key: number; v: string; d: ReturnType<typeof deferred<string>> }[] = [];
    const send = vi.fn((key: number, v: string) => {
      const d = deferred<string>();
      pending.push({ key, v, d });
      return d.promise;
    });
    const q = createLatestByKey(send);
    q.push(1, "a");
    q.push(1, "b");
    q.push(1, "c");
    expect(send).toHaveBeenCalledTimes(1);
    expect(q.busy(1)).toBe(true);
    pending[0].d.resolve("ok");
    await tick();
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1]).toEqual([1, "c"]);
    pending[1].d.resolve("ok");
    await tick();
    expect(q.busy(1)).toBe(false);
  });

  it("不同 key 互不阻擋", () => {
    const send = vi.fn(() => new Promise<void>(() => {}));
    const q = createLatestByKey(send);
    q.push(1, "a");
    q.push(2, "b");
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("寫入依呼叫順序：最後到伺服器的一定是最後 push 的值", async () => {
    let server = "";
    const ds: (() => void)[] = [];
    const q = createLatestByKey((_k: number, v: string) => new Promise<void>((r) => ds.push(() => ((server = v), r()))));
    q.push(1, "v1");
    q.push(1, "v2");
    q.push(1, "v3");
    while (ds.length) {
      ds.shift()!();
      await tick();
    }
    expect(server).toBe("v3");
  });

  it("失敗後仍會送出待送的值；只回報最新值的錯誤", async () => {
    const onError = vi.fn();
    const onSuccess = vi.fn();
    const ds: ReturnType<typeof deferred<string>>[] = [];
    const q = createLatestByKey(
      () => {
        const d = deferred<string>();
        ds.push(d);
        return d.promise;
      },
      { onError, onSuccess },
    );
    q.push(1, "a");
    q.push(1, "b");
    ds[0].reject(new Error("boom"));
    await tick();
    expect(onError).not.toHaveBeenCalled(); // 已被 b 取代
    ds[1].reject(new Error("last"));
    await tick();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBe(1);
    expect((onError.mock.calls[0][1] as Error).message).toBe("last");
    q.push(1, "c");
    ds[2].resolve("R");
    await tick();
    expect(onSuccess).toHaveBeenCalledWith(1, "R", "c");
  });

  it("send 同步丟錯或 callback 丟錯都不會卡住", async () => {
    let n = 0;
    const send = vi.fn(async () => {
      n++;
      if (n === 1) throw new Error("sync-ish");
    });
    const q = createLatestByKey(send, {
      onError: () => {
        throw new Error("cb");
      },
    });
    q.push(1, "a");
    await tick();
    expect(q.busy(1)).toBe(false);
    q.push(1, "b");
    expect(send).toHaveBeenCalledTimes(2);
    const bad = createLatestByKey(() => {
      throw new Error("sync");
    });
    bad.push(1, "x");
    await tick();
    expect(bad.busy(1)).toBe(false);
  });
});
