import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { AI_PROMPT } from "./aiPrompt";
import { ImportDialog } from "./ImportDialog";

const create = vi.fn();
const update = vi.fn();
const del = vi.fn();
let pages: unknown[];
vi.mock("@/api/hooks", async (orig) => ({
  ...(await orig<typeof import("@/api/hooks")>()),
  usePages: () => ({ data: pages }),
  useCreatePage: () => ({ mutateAsync: create }),
  useUpdatePage: () => ({ mutateAsync: update }),
  useDeletePage: () => ({ mutateAsync: del }),
}));

const pg = (id: number, position: number, scene_prompt: string, action_prompt: string) => ({
  id,
  position,
  scene_prompt,
  action_prompt,
});
const TEXT = "第1頁\n場景：森林\n動作：跑步\n第2頁\n場景：海邊\n動作：游泳\n第3頁\n場景：山\n動作：爬山\n第4頁\n這不是頁";
const TEXT_OK = "第1頁\n場景：森林\n動作：跑步\n第2頁\n場景：海邊\n動作：游泳\n第3頁\n場景：山\n動作：爬山";

function setup(text: string) {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <ImportDialog bookId={7} open onClose={onClose} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
  return onClose;
}

beforeEach(() => {
  create.mockReset().mockResolvedValue({});
  update.mockReset().mockResolvedValue({});
  del.mockReset();
  // page 1 empty, page 2 has a scene only
  pages = [pg(11, 1, "", ""), pg(12, 2, "舊場景", "")];
});

describe("ImportDialog", () => {
  it("preview summary counts", () => {
    setup(TEXT);
    expect(screen.getByText("4 段：新增 1 頁、填入 5 格、略過 1 格、1 段看不懂")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("填入場景、動作")).toBeInTheDocument();
    expect(within(rows[2]).getByText("填入動作")).toBeInTheDocument();
    expect(within(rows[3]).getByText("新增這一頁")).toBeInTheDocument();
    expect(within(rows[4]).getByText("找不到「場景：」或「動作：」")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "匯入（新增 1 頁、填入 5 格）" })).toBeEnabled();
  });

  it("shows 已有內容，略過 when nothing can be filled", () => {
    pages = [pg(11, 1, "有", "有")];
    setup("場景：森林\n動作：跑步");
    expect(screen.getByText("已有內容，略過")).toBeInTheDocument();
  });

  it("apply creates then patches and never deletes", async () => {
    const onClose = setup(TEXT_OK);
    await userEvent.click(screen.getByRole("button", { name: "匯入（新增 1 頁、填入 5 格）" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith({ scene_prompt: "山", action_prompt: "爬山" });
    expect(update).toHaveBeenCalledTimes(2);
    expect(update).toHaveBeenNthCalledWith(1, { pageId: 11, scene_prompt: "森林", action_prompt: "跑步" });
    expect(update).toHaveBeenNthCalledWith(2, { pageId: 12, action_prompt: "游泳" });
    expect(create.mock.invocationCallOrder[0]).toBeLessThan(update.mock.invocationCallOrder[0]);
    expect(del).not.toHaveBeenCalled();
  });

  it("does not apply rows with problems", async () => {
    setup(`場景：${"x".repeat(801)}\n動作：跑\n\n場景：海\n動作：游`);
    expect(screen.getByText("場景超過 800 字")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^匯入/ }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith({ pageId: 12, action_prompt: "游" });
    expect(create).not.toHaveBeenCalled();
  });

  it("取消、關閉與 Esc 在匯入中都不能關掉對話框", async () => {
    let release!: () => void;
    create.mockImplementation(() => new Promise<void>((r) => (release = r)));
    const onClose = setup(TEXT_OK);
    await userEvent.click(screen.getByRole("button", { name: "匯入（新增 1 頁、填入 5 格）" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "關閉" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    release();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("開場白與結語（沒有頁碼）各自一列，不佔頁碼", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    setup("好的，以下是分鏡：\n\n場景：森林\n動作：跑步\n\n希望對你有幫助！");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("森林")).toBeInTheDocument();
    expect(within(rows[0]).getAllByRole("cell")[0]).toHaveTextContent("1");
    expect(screen.getByText("好的，以下是分鏡：")).toBeInTheDocument();
    expect(screen.getByText("希望對你有幫助！")).toBeInTheDocument();
    expect(err.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    err.mockRestore();
  });

  it("copy prompt writes AI_PROMPT(n) to clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    setup("");
    await userEvent.click(screen.getByRole("button", { name: "複製給 AI 的指令" }));
    expect(writeText).toHaveBeenCalledWith(AI_PROMPT(2));
    expect(await screen.findByText("已複製")).toBeInTheDocument();
  });
  describe("圖下方的文字", () => {
    const CAP = "第1頁\n場景：森林\n動作：跑步\n中文：小熊\n英文：Bear\n第2頁\n場景：山\n動作：爬山\n中文：登山";

    it("預覽顯示中文字、英文字兩欄，只填空欄位", () => {
      pages = [{ ...pg(11, 1, "", ""), caption_zh: "舊", caption_en: null }, pg(12, 2, "舊場景", "")];
      setup(CAP);
      const head = screen.getAllByRole("columnheader").map((h) => h.textContent);
      expect(head).toEqual(["頁", "場景", "動作", "中文字", "英文字", "結果"]);
      const rows = screen.getAllByRole("row");
      expect(within(rows[1]).getAllByRole("cell").map((c) => c.textContent)).toEqual([
        "1", "森林", "跑步", "小熊", "Bear", "填入場景、動作、英文字",
      ]);
      expect(screen.getByRole("button", { name: "匯入（新增 0 頁、填入 5 格）" })).toBeEnabled();
    });

    it("沒有任何文字時不顯示這兩欄", () => {
      setup(TEXT_OK);
      expect(screen.queryByRole("columnheader", { name: "中文字" })).not.toBeInTheDocument();
    });

    it("套用：現有頁用 update，新增的頁先建立再用 update 補文字", async () => {
      pages = [pg(11, 1, "", "")];
      create.mockResolvedValueOnce({ id: 31 });
      const onClose = setup(CAP);
      await userEvent.click(screen.getByRole("button", { name: /^匯入/ }));
      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(update).toHaveBeenCalledWith({
        pageId: 11, scene_prompt: "森林", action_prompt: "跑步", caption_zh: "小熊", caption_en: "Bear",
      });
      expect(create).toHaveBeenCalledTimes(1);
      expect(create).toHaveBeenCalledWith({ scene_prompt: "山", action_prompt: "爬山" });
      expect(update).toHaveBeenCalledWith({ pageId: 31, caption_zh: "登山" });
    });

    it("超過上限的列顯示訊息且不套用", async () => {
      setup(`場景：森林\n動作：跑\n中文：${"字".repeat(13)}`);
      expect(screen.getByText("中文字最多 12 個字")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^匯入/ })).toBeDisabled();
    });
  });
});
