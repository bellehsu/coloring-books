import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { createAutoCompose, type AutoComposeOptions } from "./autoCompose";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function make(o: Partial<AutoComposeOptions> = {}) {
  const compose = vi.fn<(pageId: number) => Promise<void>>(async () => {});
  const hasLineEdits = vi.fn<(pageId: number) => boolean>(() => false);
  const confirm = vi.fn(async () => true);
  const ac = createAutoCompose({ delayMs: 600, compose, hasLineEdits, confirm, ...o });
  return {
    ac,
    compose: (o.compose ?? compose) as Mock<(pageId: number) => Promise<void>>,
    confirm: (o.confirm ?? confirm) as Mock<() => Promise<boolean>>,
  };
}

/** 一個可以從外面結束的 promise。 */
function deferred<T = void>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("createAutoCompose", () => {
  it("debounces bursts into one compose", async () => {
    const { ac, compose } = make();
    expect(ac.status(1)).toBe("idle");
    ac.schedule(1);
    expect(ac.status(1)).toBe("pending");
    await vi.advanceTimersByTimeAsync(400);
    ac.schedule(1); // 計時重新開始
    await vi.advanceTimersByTimeAsync(400);
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(599);
    expect(compose).not.toHaveBeenCalled();
    expect(ac.status(1)).toBe("pending");
    await vi.advanceTimersByTimeAsync(1);
    expect(compose).toHaveBeenCalledTimes(1);
    expect(compose).toHaveBeenCalledWith(1);
    expect(ac.status(1)).toBe("idle");
  });

  it("separate pages compose independently", async () => {
    const { ac, compose } = make();
    ac.schedule(2); // 第 2 頁拖完
    await vi.advanceTimersByTimeAsync(300);
    ac.schedule(3); // 600 ms 內換到第 3 頁又動了一下
    expect(ac.status(2)).toBe("pending");
    expect(ac.status(3)).toBe("pending");
    await vi.advanceTimersByTimeAsync(300);
    // 第 2 頁的計時沒被第 3 頁重設，也沒被搬到第 3 頁
    expect(compose.mock.calls).toEqual([[2]]);
    expect(ac.status(2)).toBe("idle");
    expect(ac.status(3)).toBe("pending");
    await vi.advanceTimersByTimeAsync(300);
    expect(compose.mock.calls).toEqual([[2], [3]]);
  });

  it("a page switch alone never composes the other page", async () => {
    const { ac, compose } = make();
    ac.schedule(2);
    await vi.advanceTimersByTimeAsync(5000);
    expect(compose.mock.calls).toEqual([[2]]);
    expect(ac.status(3)).toBe("idle");
  });

  it("asks once when line edits exist; cancel skips compose", async () => {
    const answers = [false, true];
    const confirm = vi.fn(async () => answers.shift()!);
    const { ac, compose } = make({ hasLineEdits: () => true, confirm });
    // 取消：不合成，回到 idle（沒有錯誤）
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(compose).not.toHaveBeenCalled();
    expect(ac.status(1)).toBe("idle");
    // 使用者沒同意過 → 下次再問（問的是這一頁）；這次繼續 → 合成
    ac.schedule(1);
    ac.schedule(1); // 一連串的變化只問一次
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(confirm).toHaveBeenLastCalledWith(1);
    expect(compose).toHaveBeenCalledTimes(1);
  });

  it("consent covers one composite: line edits drawn on the new composite are asked about again", async () => {
    let edited = true;
    const confirm = vi.fn(async () => true);
    const compose = vi.fn(async () => {
      edited = false; // 新的合成圖還沒有補過的線
    });
    const { ac } = make({ hasLineEdits: () => edited, confirm, compose });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(compose).toHaveBeenCalledTimes(1);
    // 新合成圖沒有補線：不問
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(compose).toHaveBeenCalledTimes(2);
    // 在新合成圖上補了線，再移動：要再問，取消就不合成
    edited = true;
    confirm.mockResolvedValueOnce(false);
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(compose).toHaveBeenCalledTimes(2);
  });

  it("consent survives a failed compose: the retry does not ask again", async () => {
    const confirm = vi.fn(async () => true);
    const compose = vi.fn().mockRejectedValueOnce(new Error("409")).mockResolvedValue(undefined);
    const { ac } = make({ hasLineEdits: () => true, confirm, compose });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(ac.status(1)).toBe("error");
    await ac.flush(1);
    expect(compose).toHaveBeenCalledTimes(2);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("no line edits → never asks; the other page still asks for its own edits", async () => {
    const { ac, compose, confirm } = make({ hasLineEdits: (id) => id === 2 });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).not.toHaveBeenCalled();
    expect(compose.mock.calls).toEqual([[1]]);
    ac.schedule(2);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(compose.mock.calls).toEqual([[1], [2]]);
  });

  it("while the confirmation is open the status is running", async () => {
    const answer = deferred<boolean>();
    const { ac, compose } = make({ hasLineEdits: () => true, confirm: () => answer.promise });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(ac.status(1)).toBe("running");
    answer.resolve(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(compose).toHaveBeenCalledWith(1);
    expect(ac.status(1)).toBe("idle");
  });

  it("error status and flush retries", async () => {
    let fail = true;
    const compose = vi.fn(async () => {
      if (fail) throw new Error("409");
    });
    const { ac } = make({ compose });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(compose).toHaveBeenCalledTimes(1);
    expect(ac.status(1)).toBe("error");
    // 失敗不會自己重試
    await vi.advanceTimersByTimeAsync(5000);
    expect(compose).toHaveBeenCalledTimes(1);
    fail = false;
    await ac.flush(1);
    expect(compose).toHaveBeenCalledTimes(2);
    expect(ac.status(1)).toBe("idle");
  });

  it("flush runs a pending compose now and cancels its timer", async () => {
    const { ac, compose } = make();
    ac.schedule(1);
    await ac.flush(1);
    expect(compose).toHaveBeenCalledTimes(1);
    expect(ac.status(1)).toBe("idle");
    await vi.advanceTimersByTimeAsync(1000);
    expect(compose).toHaveBeenCalledTimes(1);
  });

  it("flush on an idle page does nothing", async () => {
    const { ac, compose } = make();
    await ac.flush(1);
    expect(compose).not.toHaveBeenCalled();
  });

  it("a change while composing runs one more compose after it, never two at once", async () => {
    const first = deferred();
    const compose = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
    const { ac } = make({ compose });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(ac.status(1)).toBe("running");
    ac.schedule(1);
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    // 還在合成：第二次要等第一次結束
    expect(compose).toHaveBeenCalledTimes(1);
    expect(ac.status(1)).toBe("running");
    first.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(compose).toHaveBeenCalledTimes(2);
    expect(ac.status(1)).toBe("idle");
  });

  it("flush while composing waits for the running compose", async () => {
    const first = deferred();
    const compose = vi.fn().mockReturnValueOnce(first.promise);
    const { ac } = make({ compose });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    let done = false;
    void ac.flush(1).then(() => (done = true));
    await vi.advanceTimersByTimeAsync(0);
    expect(done).toBe(false);
    first.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(done).toBe(true);
    expect(compose).toHaveBeenCalledTimes(1);
  });

  it("schedule clears an error; subscribers hear every status change", async () => {
    const compose = vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(undefined);
    const { ac } = make({ compose });
    const seen: string[] = [];
    const off = ac.subscribe(() => seen.push(ac.status(1)));
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(ac.status(1)).toBe("error");
    ac.schedule(1);
    expect(ac.status(1)).toBe("pending");
    await vi.advanceTimersByTimeAsync(600);
    expect(ac.status(1)).toBe("idle");
    expect(seen).toEqual(["pending", "running", "error", "pending", "running", "idle"]);
    off();
    ac.schedule(1);
    expect(seen).toHaveLength(6);
  });

  it("disconnect cancels pending timers (leaving the editor); connecting again works", async () => {
    const { ac, compose } = make();
    const disconnect = ac.connect();
    ac.schedule(1);
    ac.schedule(2);
    disconnect();
    expect(ac.status(1)).toBe("idle");
    await vi.advanceTimersByTimeAsync(1000);
    expect(compose).not.toHaveBeenCalled();
    ac.schedule(1); // 卸載後才到的變化：不動作
    await vi.advanceTimersByTimeAsync(1000);
    expect(compose).not.toHaveBeenCalled();
    ac.connect(); // StrictMode 再掛上
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(compose.mock.calls).toEqual([[1]]);
  });

  it("forget cancels this page only and clears its consent", async () => {
    const hasLineEdits = () => true;
    const confirm = vi.fn(async () => true);
    const { ac, compose } = make({ hasLineEdits, confirm });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(1);
    ac.schedule(1);
    ac.schedule(2);
    ac.forget(1); // 第 1 頁刪除了
    expect(ac.status(1)).toBe("idle");
    await vi.advanceTimersByTimeAsync(600);
    expect(compose.mock.calls).toEqual([[1], [2]]);
    // 同一個 id 變成新頁：重新問
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(confirm).toHaveBeenCalledTimes(3);
  });

  it("default delay is 600 ms", async () => {
    const compose = vi.fn(async () => {});
    const ac = createAutoCompose({ compose, hasLineEdits: () => false, confirm: async () => true });
    ac.schedule(1);
    await vi.advanceTimersByTimeAsync(599);
    expect(compose).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(compose).toHaveBeenCalledTimes(1);
  });
});
