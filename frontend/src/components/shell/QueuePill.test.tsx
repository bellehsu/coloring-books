import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueuePill } from "./QueuePill";

const useQueue = vi.fn();
vi.mock("@/api/hooks", () => ({ useQueue: () => useQueue() }));

const q = (o: object) => ({ data: { state: "idle", last_error: null, queued: 0, running_id: null, ...o } });

describe("QueuePill", () => {
  beforeEach(() => useQueue.mockReset());
  it("引擎無法連線", () => {
    useQueue.mockReturnValue(q({ state: "engine_unavailable", queued: 2 }));
    render(<QueuePill />);
    expect(screen.getByText("生成引擎無法連線")).toBeInTheDocument();
  });
  it("有排隊", () => {
    useQueue.mockReturnValue(q({ state: "running", queued: 2 }));
    render(<QueuePill />);
    expect(screen.getByText("生成佇列 2 張")).toBeInTheDocument();
  });
  it("執行中且無排隊也算 1 張", () => {
    useQueue.mockReturnValue(q({ state: "running", running_id: 5 }));
    render(<QueuePill />);
    expect(screen.getByText("生成佇列 1 張")).toBeInTheDocument();
  });
  it("空閒", () => {
    useQueue.mockReturnValue(q({}));
    render(<QueuePill />);
    expect(screen.getByText("佇列空閒")).toBeInTheDocument();
  });
  it("尚未載入時顯示空閒", () => {
    useQueue.mockReturnValue({ data: undefined });
    render(<QueuePill />);
    expect(screen.getByText("佇列空閒")).toBeInTheDocument();
  });
});
