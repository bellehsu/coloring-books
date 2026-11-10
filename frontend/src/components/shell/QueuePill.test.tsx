import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueuePill } from "./QueuePill";

const useQueue = vi.fn();
vi.mock("@/api/hooks", () => ({ useQueue: () => useQueue() }));

type Kind = "character" | "background" | "pose";
const q = (o: object = {}, pending: Kind[] = [], recent: object[] = []) => ({
  data: {
    state: "idle",
    last_error: null,
    queued: 0,
    running_id: null,
    pending: pending.map((kind) => ({ kind })),
    recent,
    ...o,
  },
});
const busy = (n: number, kind: Kind = "pose", recent: object[] = []) =>
  q({ state: "running", queued: n - 1, running_id: 1 }, Array(n).fill(kind), recent);

describe("QueuePill", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useQueue.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("引擎無法連線／服務已停止（含 last_error）", () => {
    useQueue.mockReturnValue(q({ state: "engine_unavailable", queued: 2 }, ["pose", "pose"]));
    const { rerender } = render(<QueuePill />);
    expect(screen.getByText("生成引擎無法連線")).toBeInTheDocument();
    useQueue.mockReturnValue(q({ state: "stopped", last_error: "boom" }));
    rerender(<QueuePill />);
    expect(screen.getByText("生成服務已停止")).toBeInTheDocument();
    expect(screen.getByText("（boom）")).toBeInTheDocument();
  });

  it("尚未載入時顯示閒置", () => {
    useQueue.mockReturnValue({ data: undefined });
    render(<QueuePill />);
    expect(screen.getByText("GPU 閒置")).toBeInTheDocument();
  });

  it("pill shows 閒置 / 生成中 2/6 · 剩約 2 分鐘 / 就緒 then 閒置", () => {
    useQueue.mockReturnValue(q());
    const { rerender } = render(<QueuePill />);
    expect(screen.getByText("GPU 閒置")).toBeInTheDocument();

    // 6 張進來，每張估 25 秒
    useQueue.mockReturnValue(busy(6));
    rerender(<QueuePill />);
    expect(screen.getByText("生成中 1/6 · 剩約 3 分鐘")).toBeInTheDocument();

    // 完成 1 張：剩 5 張 × 25 s = 125 s → 2/6、剩約 3 分鐘；用較短的歷史耗時
    useQueue.mockReturnValue(busy(5, "pose", [{ kind: "pose", elapsed_s: 24 }]));
    rerender(<QueuePill />);
    expect(screen.getByText("生成中 2/6 · 剩約 2 分鐘")).toBeInTheDocument();

    // 剩最後一張且不到 1 分鐘
    useQueue.mockReturnValue(busy(1));
    rerender(<QueuePill />);
    expect(screen.getByText("生成中 6/6 · 剩不到 1 分鐘")).toBeInTheDocument();

    useQueue.mockReturnValue(q());
    rerender(<QueuePill />);
    expect(screen.getByText("GPU 就緒")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(5 * 60_000 - 1));
    expect(screen.getByText("GPU 就緒")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(2));
    expect(screen.getByText("GPU 閒置")).toBeInTheDocument();
  });

  it("中途再加入工作時 m 變大；清空後重新計數", () => {
    useQueue.mockReturnValue(busy(2));
    const { rerender } = render(<QueuePill />);
    expect(screen.getByText(/生成中 1\/2/)).toBeInTheDocument();
    useQueue.mockReturnValue(busy(1));
    rerender(<QueuePill />);
    expect(screen.getByText(/生成中 2\/2/)).toBeInTheDocument();
    useQueue.mockReturnValue(busy(3)); // 又加入 2 張
    rerender(<QueuePill />);
    expect(screen.getByText(/生成中 2\/4/)).toBeInTheDocument();
    useQueue.mockReturnValue(q());
    rerender(<QueuePill />);
    useQueue.mockReturnValue(busy(1));
    rerender(<QueuePill />);
    expect(screen.getByText(/生成中 1\/1/)).toBeInTheDocument();
  });
});
