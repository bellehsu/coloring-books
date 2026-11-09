import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Book, Generation, Page, QA } from "@/api/client";
import { qk } from "@/api/hooks";
import { ToastProvider } from "@/components/ui/Toast";
import { editsQueueFor } from "./editsQueue";
import { QACompare } from "./QACompare";
import { QAPanel } from "./QAPanel";

type O = Record<string, unknown>;
const book = { id: 1, character_generation_id: 99 } as unknown as Book;
const mkPage = (o: O = {}) =>
  ({ id: 7, book_id: 1, selected_generation_id: 5, status: "draft", character_check: null, lineart_url: "/api/pages/7/lineart?v=k1", ...o }) as unknown as Page;
const gen = { id: 5, image_url: "/img/5.png" } as unknown as Generation;
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

function setup(page: Page, qa: QA | undefined) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <QAPanel book={book} page={page} qa={qa} generation={gen} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}
const passBtn = () => screen.getByRole("button", { name: /標為通過|已通過/ });
const posts = (u: string) => calls.filter((c) => c.method === "POST" && c.url === u);

describe("QAPanel", () => {
  it("未確認角色：通過按鈕停用並顯示「請先確認角色外型。」", () => {
    setup(mkPage(), mkQA());
    expect(passBtn()).toBeDisabled();
    expect(screen.getByText("請先確認角色外型。")).toBeInTheDocument();
    expect(screen.queryByText(/強制通過/)).not.toBeInTheDocument();
  });

  it("character-check 帶目前選定圖的 generation_id 與註記", async () => {
    setup(mkPage({ selected_generation_id: 5 }), mkQA());
    await userEvent.type(screen.getByLabelText(/註記/), "耳朵偏短");
    await userEvent.click(screen.getByRole("radio", { name: "一致" }));
    await waitFor(() => expect(posts("/api/pages/7/character-check")).toHaveLength(1));
    expect(posts("/api/pages/7/character-check")[0].body).toEqual({
      generation_id: 5,
      result: "consistent",
      note: "耳朵偏短",
    });
  });

  it("character-check 409：顯示訊息並重新抓頁面", async () => {
    responses["POST /api/pages/7/character-check"] = () => j({ detail: "選定的圖片已更換，請重新檢查" }, 409);
    const qc = setup(mkPage(), mkQA());
    const inv = vi.spyOn(qc, "invalidateQueries");
    await userEvent.click(screen.getByRole("radio", { name: "一致" }));
    expect(await screen.findByText("選定的圖片已更換，請重新檢查")).toBeInTheDocument();
    await waitFor(() => expect(inv).toHaveBeenCalledWith({ queryKey: qk.pages(1) }));
  });

  it("不一致：沒有強制通過，顯示換圖提示", () => {
    setup(mkPage({ character_check: "inconsistent" }), mkQA({ errors: [msg("gap", "有缺口")], gap_count: 1 }));
    expect(passBtn()).toBeDisabled();
    expect(screen.getByText("角色不一致不能通過，請換一張候選圖。")).toBeInTheDocument();
    expect(screen.queryByText(/強制通過/)).not.toBeInTheDocument();
  });

  it("有缺口但角色一致：錯誤清單、補線按鈕、強制通過需填原因", async () => {
    setup(
      mkPage({ character_check: "consistent" }),
      mkQA({ errors: [msg("gap", "3 個缺口，塗色會漏出去")], warnings: [msg("small", "小區域 62 個")], gap_count: 3 }),
    );
    expect(screen.getByText("3 個缺口，塗色會漏出去")).toBeInTheDocument();
    expect(screen.getByText("小區域 62 個")).toBeInTheDocument();
    expect(screen.getByText("還有缺口：修好再通過，或填寫原因強制通過。")).toBeInTheDocument();
    expect(passBtn()).toBeDisabled();
    expect(screen.getByRole("button", { name: "自動補線" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /強制通過/ }));
    const dlg = screen.getByRole("dialog");
    const submit = within(dlg).getByRole("button", { name: "強制通過" });
    await userEvent.click(submit);
    expect(posts("/api/pages/7/pass")).toHaveLength(0);
    expect(within(dlg).getByText("請填寫原因")).toBeInTheDocument();
    await userEvent.type(within(dlg).getByLabelText(/原因/), "  線稿刻意留白  ");
    await userEvent.click(submit);
    await waitFor(() => expect(posts("/api/pages/7/pass")).toHaveLength(1));
    expect(posts("/api/pages/7/pass")[0].body).toEqual({ force_reason: "線稿刻意留白" });
  });

  it("原因最多 500 字", async () => {
    setup(mkPage({ character_check: "consistent" }), mkQA({ errors: [msg("gap", "x")], gap_count: 1 }));
    await userEvent.click(screen.getByRole("button", { name: /強制通過/ }));
    expect(within(screen.getByRole("dialog")).getByLabelText(/原因/)).toHaveAttribute("maxlength", "500");
  });

  it("一致且沒有錯誤：可通過，送出空 force_reason", async () => {
    setup(mkPage({ character_check: "consistent" }), mkQA());
    expect(screen.getByText("可以通過了。")).toBeInTheDocument();
    await userEvent.click(passBtn());
    await waitFor(() => expect(posts("/api/pages/7/pass")).toHaveLength(1));
    expect(posts("/api/pages/7/pass")[0].body).toEqual({ force_reason: null });
  });

  it("pass 409 的 errors 列在畫面上", async () => {
    responses["POST /api/pages/7/pass"] = () =>
      j({ detail: { message: "自動檢查有錯誤", errors: [msg("gap", "還有 2 個缺口")] } }, 409);
    setup(mkPage({ character_check: "consistent" }), mkQA());
    await userEvent.click(passBtn());
    expect(await screen.findByText("還有 2 個缺口")).toBeInTheDocument();
  });

  it("已通過：退回草稿 → POST unpass", async () => {
    setup(mkPage({ character_check: "consistent", status: "passed" }), mkQA({ status: "passed" }));
    expect(screen.getByText("這頁會出現在匯出的 PDF 裡。")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "已通過・點此退回草稿" }));
    await waitFor(() => expect(posts("/api/pages/7/unpass")).toHaveLength(1));
  });

  it("自動補線與通過前先等修線寫入完成；flush 失敗則不送", async () => {
    const qc = setup(mkPage({ character_check: "consistent" }), mkQA({ errors: [msg("gap", "x")], gap_count: 1 }));
    const e = editsQueueFor(qc, 1, 7, 5);
    const flush = vi.spyOn(e, "flush").mockRejectedValue(new Error("write failed"));
    await userEvent.click(screen.getByRole("button", { name: "自動補線" }));
    expect(flush).toHaveBeenCalled();
    expect(posts("/api/pages/7/lineart/autofix")).toHaveLength(0);
    flush.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: "自動補線" }));
    await waitFor(() => expect(posts("/api/pages/7/lineart/autofix")).toHaveLength(1));
    expect(posts("/api/pages/7/lineart/autofix")[0].body).toEqual({ generation_id: 5 });
  });

  it("先打註記再選結果：選之前顯示提示，選了才送出註記", async () => {
    setup(mkPage(), mkQA());
    expect(screen.queryByText("選擇一致或不一致後才會儲存註記")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/註記/), "顏色偏淡");
    expect(screen.getByText("選擇一致或不一致後才會儲存註記")).toBeInTheDocument();
    expect(posts("/api/pages/7/character-check")).toHaveLength(0);
    await userEvent.click(screen.getByRole("radio", { name: "不一致" }));
    await waitFor(() => expect(posts("/api/pages/7/character-check")).toHaveLength(1));
    expect(posts("/api/pages/7/character-check")[0].body).toMatchObject({ result: "inconsistent", note: "顏色偏淡" });
  });

  it("連點「標為通過」只送一次 POST（flush 等待期間也擋）", async () => {
    const qc = setup(mkPage({ character_check: "consistent" }), mkQA());
    const e = editsQueueFor(qc, 1, 7, 5);
    let release!: () => void;
    vi.spyOn(e, "flush").mockReturnValue(new Promise<undefined>((r) => (release = () => r(undefined))));
    await userEvent.dblClick(passBtn());
    release();
    await waitFor(() => expect(posts("/api/pages/7/pass")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(posts("/api/pages/7/pass")).toHaveLength(1);
  });

  it("連點「自動補線」只送一次 POST", async () => {
    const qc = setup(mkPage({ character_check: "consistent" }), mkQA({ errors: [msg("gap", "x")], gap_count: 1 }));
    const e = editsQueueFor(qc, 1, 7, 5);
    let release!: () => void;
    vi.spyOn(e, "flush").mockReturnValue(new Promise<undefined>((r) => (release = () => r(undefined))));
    await userEvent.dblClick(screen.getByRole("button", { name: "自動補線" }));
    release();
    await waitFor(() => expect(posts("/api/pages/7/lineart/autofix")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(posts("/api/pages/7/lineart/autofix")).toHaveLength(1);
  });

  it("失焦存註記進行中，通過按鈕與單選仍可點", async () => {
    let release!: () => void;
    responses["POST /api/pages/7/character-check"] = () => j({});
    const gate = new Promise<void>((r) => (release = r));
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, url, body: typeof init.body === "string" ? JSON.parse(init.body) : undefined });
      if (url === "/api/pages/7/character-check") await gate;
      return j({});
    });
    setup(mkPage({ character_check: "consistent" }), mkQA({ qa_note: "舊" }));
    await userEvent.type(screen.getByLabelText(/註記/), "新");
    await userEvent.tab();
    await waitFor(() => expect(posts("/api/pages/7/character-check")).toHaveLength(1));
    expect(passBtn()).toBeEnabled();
    expect(screen.getByRole("radio", { name: "不一致" })).toBeEnabled();
    release();
  });

  it("下載本頁 PNG 連結", () => {
    setup(mkPage(), mkQA());
    const a = screen.getByRole("link", { name: /下載本頁 PNG/ });
    expect(a).toHaveAttribute("href", "/api/pages/7/print.png");
    expect(a).toHaveAttribute("download");
  });
});

describe("QACompare", () => {
  it("三張圖：角色參考、彩圖、疊圖（?v= 取自 qa.lineart_url）", () => {
    render(<QACompare book={book} page={mkPage()} qa={mkQA()} generation={gen} />);
    expect(screen.getByAltText("本書角色參考圖")).toHaveAttribute("src", "/api/generations/99/image");
    expect(screen.getByAltText("本頁彩圖")).toHaveAttribute("src", "/img/5.png");
    expect(screen.getByAltText(/線稿，紅圈/)).toHaveAttribute("src", "/api/pages/7/qa/overlay.png?v=k2");
  });
  it("qa 未載入時用 page.lineart_url 的 key", () => {
    render(<QACompare book={book} page={mkPage()} qa={undefined} generation={gen} />);
    expect(screen.getByAltText(/線稿，紅圈/)).toHaveAttribute("src", "/api/pages/7/qa/overlay.png?v=k1");
  });
});
