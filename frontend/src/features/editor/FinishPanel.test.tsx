import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Book, Page, QA } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { editsQueueFor } from "@/features/editing/editsQueue";
import { FinishPanel, type FinishPanelProps } from "./FinishPanel";

type O = Record<string, unknown>;
const book = { id: 1, character_generation_id: 99 } as unknown as Book;
const mkPage = (o: O = {}) =>
  ({
    id: 7,
    book_id: 1,
    selected_generation_id: 5,
    status: "draft",
    character_check: null,
    line_detail: "standard",
    line_width_px: 11,
    lineart_url: "/api/pages/7/lineart?v=k1",
    ...o,
  }) as unknown as Page;
const msg = (code: string, message: string) => ({ code, message });
const mkQA = (o: O = {}) =>
  ({
    status: "draft",
    errors: [],
    warnings: [],
    gaps: [],
    gap_count: 0,
    character_check: null,
    qa_note: "",
    forced_pass_reason: null,
    lineart_url: "/api/pages/7/lineart?v=k2",
    ...o,
  }) as unknown as QA;
const gapQA = (n: number, o: O = {}) =>
  mkQA({
    errors: [msg("gaps", `${n} 個缺口`)],
    gaps: Array.from({ length: n }, (_, i) => [0.1 * (i + 1), 0.5]),
    gap_count: n,
    ...o,
  });

let calls: { method: string; url: string; body?: unknown }[];
let responses: Record<string, () => Response>;
const j = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  calls = [];
  responses = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, url, body: typeof init.body === "string" ? JSON.parse(init.body) : undefined });
      const r = responses[`${method} ${url}`];
      return r ? r() : j({}, 200);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function setup(page: Page, qa: QA | undefined, over: Partial<FinishPanelProps> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props: FinishPanelProps = {
    book,
    page,
    qa,
    onPatch: vi.fn(async () => ({})),
    onGoCompose: vi.fn(),
    ...over,
  };
  const r = render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <FinishPanel {...props} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { qc, props, ...r };
}
const finishBtn = () => screen.getByRole("button", { name: "完成這一頁 ✓" });
const posts = (u: string) => calls.filter((c) => c.method === "POST" && c.url === u);

describe("FinishPanel", () => {
  it("shows gap count and autofix", async () => {
    setup(mkPage(), gapQA(3, { warnings: [msg("small", "小區域 62 個")] }));
    expect(screen.getByText("還有 3 個缺口")).toBeInTheDocument();
    expect(screen.getByText("橘色圈起來的地方線條沒有接起來，小朋友塗色時顏色會漏出去。")).toBeInTheDocument();
    expect(screen.getByText("自動補不好的，用「補線」筆在畫面上畫一筆接起來。")).toBeInTheDocument();
    // 缺口以外的警告：小字清單
    expect(within(screen.getByRole("list", { name: "其他提醒" })).getByText("小區域 62 個")).toBeInTheDocument();
    // 沒有掛著的修線畫布：直接呼叫自動補線（帶選定圖）
    responses["POST /api/pages/7/lineart/autofix"] = () => j({ ...gapQA(0), autofix_added: 3 });
    await userEvent.click(screen.getByRole("button", { name: "自動補線" }));
    await waitFor(() => expect(posts("/api/pages/7/lineart/autofix")).toHaveLength(1));
    expect(posts("/api/pages/7/lineart/autofix")[0].body).toEqual({ generation_id: 5 });
    expect(await screen.findByText("已補 3 條線")).toBeInTheDocument();
  });

  it("autofix goes through the mounted line canvas when there is one", async () => {
    const { qc } = setup(mkPage(), gapQA(2));
    const run = vi.fn(async () => 2);
    editsQueueFor(qc, 1, 7, 5).setAutofix(run);
    await userEvent.click(screen.getByRole("button", { name: "自動補線" }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(posts("/api/pages/7/lineart/autofix")).toHaveLength(0);
    expect(await screen.findByText("已補 2 條線")).toBeInTheDocument();
  });

  it("no gaps shows 線條都接好了", () => {
    setup(mkPage(), mkQA({ warnings: [msg("small", "小區域 62 個")] }));
    expect(screen.getByText("✓ 線條都接好了")).toBeInTheDocument();
    expect(screen.getByText("每個區塊都是封閉的，可以放心塗色。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "自動補線" })).not.toBeInTheDocument();
    expect(screen.queryByText(/還有 \d+ 個缺口/)).not.toBeInTheDocument();
    expect(screen.getByText("小區域 62 個")).toBeInTheDocument();
  });

  it("QA 還沒載入：檢查中", () => {
    setup(mkPage(), undefined);
    expect(screen.getByText("檢查中…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "自動補線" })).not.toBeInTheDocument();
  });

  it("完成 disabled until character consistent", () => {
    const { unmount } = setup(mkPage(), mkQA());
    expect(finishBtn()).toBeDisabled();
    expect(finishBtn()).toHaveAccessibleDescription("請先回答角色對不對");
    unmount();
    const r2 = setup(mkPage({ character_check: "inconsistent" }), mkQA());
    expect(finishBtn()).toBeDisabled();
    r2.unmount();
    setup(mkPage({ character_check: "consistent" }), mkQA());
    expect(finishBtn()).toBeEnabled();
    expect(screen.queryByText("請先回答角色對不對")).not.toBeInTheDocument();
  });

  it("「對」records consistent with the selected generation and keeps the saved note", async () => {
    setup(mkPage(), mkQA({ qa_note: "耳朵偏短" }));
    const card = screen.getByRole("region", { name: "角色看起來對嗎？" });
    expect(within(card).getByText("臉、耳朵、衣服跟角色一樣，而且沒有多出別的角色。")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "對" }));
    await waitFor(() => expect(posts("/api/pages/7/character-check")).toHaveLength(1));
    expect(posts("/api/pages/7/character-check")[0].body).toEqual({
      generation_id: 5,
      result: "consistent",
      note: "耳朵偏短",
    });
  });

  it("完成 without gaps posts /accept right away (same as C's 這頁可以印了), not /pass", async () => {
    setup(mkPage({ character_check: "consistent" }), mkQA());
    await userEvent.click(finishBtn());
    await waitFor(() => expect(posts("/api/pages/7/accept")).toHaveLength(1));
    expect(posts("/api/pages/7/pass")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("完成 with gaps asks once then posts /accept", async () => {
    setup(mkPage({ character_check: "consistent" }), gapQA(2));
    await userEvent.click(finishBtn());
    const dlg = await screen.findByRole("dialog");
    expect(dlg).toHaveTextContent("還有 2 個缺口，小朋友塗色時顏色可能漏出去。確定完成嗎？");
    expect(within(dlg).queryByRole("textbox")).not.toBeInTheDocument();
    // 再補補看：關掉，不送出
    await userEvent.click(within(dlg).getByRole("button", { name: "再補補看" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts("/api/pages/7/accept")).toHaveLength(0);
    // 再按一次 → 完成
    await userEvent.click(finishBtn());
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "完成" }));
    await waitFor(() => expect(posts("/api/pages/7/accept")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("accept 失敗：顯示訊息", async () => {
    responses["POST /api/pages/7/accept"] = () => j({ detail: "這一頁還在重新合成，請等它做完或按「再試一次」" }, 409);
    setup(mkPage({ character_check: "consistent" }), mkQA());
    await userEvent.click(finishBtn());
    expect(await screen.findByText("這一頁還在重新合成，請等它做完或按「再試一次」")).toBeInTheDocument();
  });

  it("不太像 records inconsistent and goes to compose", async () => {
    const { props } = setup(mkPage({ character_check: "consistent" }), mkQA());
    await userEvent.click(screen.getByRole("button", { name: "不太像，換一張" }));
    await waitFor(() => expect(posts("/api/pages/7/character-check")).toHaveLength(1));
    expect(posts("/api/pages/7/character-check")[0].body).toMatchObject({ generation_id: 5, result: "inconsistent" });
    await waitFor(() => expect(props.onGoCompose).toHaveBeenCalledTimes(1));
  });

  it("passed page shows 已完成 and 取消完成", async () => {
    setup(mkPage({ status: "passed", character_check: "consistent" }), mkQA({ status: "passed" }));
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "完成這一頁 ✓" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "取消完成" }));
    await waitFor(() => expect(posts("/api/pages/7/unpass")).toHaveLength(1));
  });

  it("通過但 C 說請看一下（attention）：不算完成，可以按「完成這一頁 ✓」清掉（POST /accept）", async () => {
    setup(mkPage({ status: "passed", attention: "failed", character_check: "consistent" }), mkQA({ status: "passed" }));
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
    await userEvent.click(finishBtn());
    await waitFor(() => expect(posts("/api/pages/7/accept")).toHaveLength(1));
  });

  it("通過＋軟標記 review_pose：算已完成（不擋），附上請看一下的說明；「看過了 ✓」清掉（POST /accept）", async () => {
    setup(
      mkPage({ status: "passed", attention: "review_pose", attention_detail: "請看一下：換了新的主角動作" }),
      mkQA({ status: "passed" }),
    );
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "完成這一頁 ✓" })).not.toBeInTheDocument();
    expect(screen.getByText("請看一下：換了新的主角動作")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消完成" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "看過了 ✓" }));
    await waitFor(() => expect(posts("/api/pages/7/accept")).toHaveLength(1));
  });

  it("通過＋review_pose 但正在重新合成（快取過期的情況）：不顯示「看過了 ✓」", () => {
    setup(
      mkPage({ status: "passed", attention: "review_pose", recompose_pending: true }),
      mkQA({ status: "passed" }),
    );
    expect(screen.queryByRole("button", { name: "看過了 ✓" })).not.toBeInTheDocument();
  });

  it("「看過了 ✓」回 409（頁面在另一個分頁改了）：提示並重抓頁面", async () => {
    responses["POST /api/pages/7/accept"] = () => j({ detail: "這一頁正在重新合成，請等一下" }, 409);
    const { qc } = setup(
      mkPage({ status: "passed", attention: "review_pose", attention_detail: "請看一下" }),
      mkQA({ status: "passed" }),
    );
    const spy = vi.spyOn(qc, "invalidateQueries");
    await userEvent.click(screen.getByRole("button", { name: "看過了 ✓" }));
    expect(await screen.findByText("這一頁正在重新合成，請等一下")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith({ queryKey: ["pages", 1] });
  });

  it("通過、沒有請看一下：沒有「看過了」", () => {
    setup(mkPage({ status: "passed", character_check: "consistent" }), mkQA({ status: "passed" }));
    expect(screen.queryByRole("button", { name: "看過了 ✓" })).not.toBeInTheDocument();
  });

  it("完成前等兩邊（進階編輯與 C）同一張圖的修線都寫完", async () => {
    let release!: () => void;
    responses["PUT /api/pages/7/lineart/edits?oneclick=true"] = () => j(mkQA());
    const { qc } = setup(mkPage({ character_check: "consistent" }), mkQA());
    const gate = new Promise<void>((r) => (release = r));
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url, init) => {
      if (String(url).includes("oneclick")) await gate;
      return base(url as string, init);
    });
    editsQueueFor(qc, 1, 7, 5, { oneclick: true }).push([{ op: "draw", width_px: 11, points: [[0.5, 0.5]] }]);
    await userEvent.click(finishBtn());
    await new Promise((r) => setTimeout(r, 20));
    expect(posts("/api/pages/7/accept")).toHaveLength(0);
    release();
    await waitFor(() => expect(posts("/api/pages/7/accept")).toHaveLength(1));
  });

  it("線條多寡：簡單／標準／細緻 立即存", async () => {
    const { props } = setup(mkPage(), mkQA());
    const group = screen.getByRole("radiogroup", { name: "線條多寡" });
    expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual(["簡單", "標準", "細緻"]);
    expect(within(group).getByRole("radio", { name: "標準" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(group).getByRole("radio", { name: "細緻" }));
    expect(props.onPatch).toHaveBeenCalledWith({ line_detail: "detailed" });
  });

  it("line width slider is inside 進階 details", async () => {
    const { props } = setup(mkPage(), mkQA());
    const slider = screen.getByLabelText(/線寬/);
    const details = slider.closest("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
    expect(details!.querySelector("summary")).toHaveTextContent("進階：線寬");
    expect(screen.getByText("11 px・約 0.9 mm")).toBeInTheDocument();
    await userEvent.click(within(details!).getByText("進階：線寬"));
    expect(details!.open).toBe(true);
    fireEvent.change(slider, { target: { value: "12" } });
    expect(props.onPatch).not.toHaveBeenCalled();
    expect(screen.getByText("12 px・約 1.0 mm")).toBeInTheDocument();
    fireEvent.keyUp(slider, { key: "ArrowRight" });
    expect(props.onPatch).toHaveBeenCalledWith({ line_width_px: 12 });
  });
});
