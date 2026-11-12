import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QA } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { peekEditsQueue } from "./editsQueue";
import { LineFixStage } from "./LineFixStage";

const qa = (gaps: [number, number][]) =>
  ({
    status: "draft",
    errors: gaps.length ? [{ code: "gaps", message: `${gaps.length} 個缺口` }] : [],
    warnings: [],
    gaps,
    gap_count: gaps.length,
    character_check: null,
    qa_note: "",
    forced_pass_reason: null,
    lineart_url: "/api/pages/7/lineart?v=a",
  }) as unknown as QA;

beforeEach(() => {
  // jsdom 沒有 canvas：畫布只需要一個什麼都不做的 2D context
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => new Proxy({}, { get: () => () => {} }) as unknown as CanvasRenderingContext2D,
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ generation_id: 5, strokes: [] }), { status: 200 })),
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setup(gaps: [number, number][]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <LineFixStage
          bookId={1}
          pageId={7}
          genId={5}
          qa={qa(gaps)}
          contentPx={[2551, 3579]}
          lineWidthPx={11}
          zoom={1}
          onZoomChange={() => {}}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc, ...r };
}

describe("LineFixStage", () => {
  it("LineFixStage renders one circle per gap", async () => {
    const { container } = setup([
      [0.25, 0.5],
      [0.75, 0.1],
      [0.5, 0.9],
    ]);
    await screen.findByRole("toolbar", { name: "修線工具" });
    const rings = container.querySelectorAll<HTMLElement>('[data-part="gap"]');
    expect(rings).toHaveLength(3);
    expect([...rings].map((r) => [r.style.left, r.style.top])).toEqual([
      ["25%", "50%"],
      ["75%", "10%"],
      ["50%", "90%"],
    ]);
  });

  it("沒有缺口：沒有圈", async () => {
    const { container } = setup([]);
    await screen.findByRole("toolbar", { name: "修線工具" });
    expect(container.querySelectorAll('[data-part="gap"]')).toHaveLength(0);
  });

  it("自動補線不在畫布工具列（在右邊面板）；畫布掛著時把自己的自動補線登記給面板用", async () => {
    const { qc, unmount } = setup([[0.5, 0.5]]);
    const bar = await screen.findByRole("toolbar", { name: "修線工具" });
    expect(within(bar).queryByRole("button", { name: "自動補線" })).not.toBeInTheDocument();
    expect(peekEditsQueue(qc, 7, 5)?.autofix).toBeTypeOf("function");
    unmount();
    expect(peekEditsQueue(qc, 7, 5)?.autofix).toBeNull();
  });
});
