import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { AI_PROMPT } from "./aiPrompt";
import { noCharacterScene } from "@/lib/noCharacterScene";
import { StorySection } from "./StorySection";
import type { PageMaterials, PreviewEntry, StoryPreview } from "./storyPreview";

describe("StorySection count", () => {
  it("shows how many paragraphs will not be imported", () => {
    const preview: StoryPreview = {
      entries: [],
      rows: [],
      pasted: 3,
      problems: [{ n: 2, text: "找不到「場景：」或「動作：」" }],
    };
    render(
      <ToastProvider>
        <StorySection
          preview={preview}
          text="x"
          onText={vi.fn()}
          uploads={new Map()}
          onUpload={vi.fn()}
          onAddPage={vi.fn()}
          onDraftChange={vi.fn()}
          onDraftRemove={vi.fn()}
          onSavePage={vi.fn()}
          existing={0}
          disabled={false}
        />
      </ToastProvider>,
    );
    expect(screen.getByText("讀到 3 段，1 段不會匯入")).toBeInTheDocument();
  });
});

describe("StorySection copy prompt", () => {
  const preview: StoryPreview = { entries: [], rows: [], pasted: 0, problems: [] };
  function mount() {
    render(
      <ToastProvider>
        <StorySection
          preview={preview}
          text=""
          onText={vi.fn()}
          uploads={new Map()}
          onUpload={vi.fn()}
          onAddPage={vi.fn()}
          onDraftChange={vi.fn()}
          onDraftRemove={vi.fn()}
          onSavePage={vi.fn()}
          existing={0}
          disabled={false}
        />
      </ToastProvider>,
    );
  }
  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
    Reflect.deleteProperty(window, "isSecureContext");
    Reflect.deleteProperty(document, "execCommand");
  });
  function env(ok: boolean) {
    Object.defineProperty(window, "isSecureContext", { value: ok, configurable: true });
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(async () => (ok ? undefined : Promise.reject(new Error("x")))) },
      configurable: true,
    });
    (document as unknown as { execCommand: unknown }).execCommand = vi.fn(() => false);
  }

  it("copy fails -> dialog with the whole prompt selected", async () => {
    env(false);
    mount();
    await userEvent.click(screen.getByRole("button", { name: "複製給 AI 的指令" }));
    const area = (await screen.findByLabelText("給 AI 的指令")) as HTMLTextAreaElement;
    expect(area.value).toBe(AI_PROMPT(10));
    expect(area.selectionStart).toBe(0);
    expect(area.selectionEnd).toBe(area.value.length);
    expect(screen.getByText("已全選，按 Ctrl＋C（手機長按）複製")).toBeInTheDocument();
    expect(screen.queryByText("無法複製，請手動選取")).toBeNull();
  });

  it("copy ok -> success toast, no dialog", async () => {
    env(true);
    mount();
    await userEvent.click(screen.getByRole("button", { name: "複製給 AI 的指令" }));
    expect(await screen.findByText("已複製，貼到 ChatGPT 或 Claude，再接上你的故事")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("StorySection edit existing pages", () => {
  const mats = (o: Partial<PageMaterials> = {}): PageMaterials => ({
    hasBackground: true,
    backgroundUploaded: false,
    hasPose: true,
    redrawBackground: false,
    redrawPose: false,
    ...o,
  });
  const entry = (o: Partial<PreviewEntry> = {}): PreviewEntry => ({
    n: 1,
    key: "p1",
    draft: null,
    pageId: 7,
    scene: "公園",
    action: "揮手",
    captionZh: "你好",
    captionEn: "Hi",
    materials: mats(),
    autoCleanup: false,
    includeCharacter: true,
    characterLocked: false,
    ...o,
  });
  function mount(e: PreviewEntry, onSavePage = vi.fn(async () => {})) {
    render(
      <ToastProvider>
        <StorySection
          preview={{ entries: [e], rows: [], pasted: 0, problems: [] }}
          text=""
          onText={vi.fn()}
          uploads={new Map()}
          onUpload={vi.fn()}
          onAddPage={vi.fn()}
          onDraftChange={vi.fn()}
          onDraftRemove={vi.fn()}
          onSavePage={onSavePage}
          existing={1}
          disabled={false}
        />
      </ToastProvider>,
    );
    return onSavePage;
  }

  it("existing rows have 編輯; 儲存 sends only the changed fields", async () => {
    const save = mount(entry());
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    const action = screen.getByLabelText("第 1 頁動作");
    await userEvent.clear(action);
    await userEvent.type(action, "跳舞");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(save).toHaveBeenCalledWith(7, { action_prompt: "跳舞" });
    expect(screen.queryByRole("button", { name: "儲存" })).toBeNull();
  });

  it("取消 discards the inputs", async () => {
    const save = mount(entry());
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    await userEvent.type(screen.getByLabelText("第 1 頁場景"), "xx");
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    expect(screen.getByLabelText("第 1 頁場景")).toHaveValue("公園");
  });

  it("failed save toasts and stays in edit mode", async () => {
    mount(entry(), vi.fn(async () => Promise.reject(new Error("boom"))));
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    await userEvent.type(screen.getByLabelText("第 1 頁中文"), "好");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(await screen.findByText(/boom|沒辦法儲存這一頁/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "儲存" })).toBeInTheDocument();
  });

  it("while the section is disabled the edit inputs and 儲存 are disabled; 取消 still works", async () => {
    const props = {
      preview: { entries: [entry()], rows: [], pasted: 0, problems: [] },
      text: "",
      onText: vi.fn(),
      uploads: new Map(),
      onUpload: vi.fn(),
      onAddPage: vi.fn(),
      onDraftChange: vi.fn(),
      onDraftRemove: vi.fn(),
      onSavePage: vi.fn(async () => {}),
      existing: 1,
    };
    const { rerender } = render(
      <ToastProvider>
        <StorySection {...props} disabled={false} />
      </ToastProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    rerender(
      <ToastProvider>
        <StorySection {...props} disabled />
      </ToastProvider>,
    );
    for (const name of ["場景", "動作", "中文", "英文"]) expect(screen.getByLabelText(`第 1 頁${name}`)).toBeDisabled();
    expect(screen.getByRole("button", { name: "儲存" })).toBeDisabled();
    const cancel = screen.getByRole("button", { name: "取消" });
    expect(cancel).toBeEnabled();
    await userEvent.click(cancel);
    expect(screen.queryByRole("button", { name: "儲存" })).toBeNull();
  });

  it("要重畫 chip when redraw_pose or redraw_background", () => {
    mount(entry({ materials: mats({ redrawPose: true }) }));
    expect(screen.getByText("要重畫")).toBeInTheDocument();
  });

  it("uploaded background row shows 背景是你上傳的圖，不會重畫 in edit mode", async () => {
    mount(entry({ materials: mats({ backgroundUploaded: true }) }));
    expect(screen.queryByText("背景是你上傳的圖，不會重畫")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    expect(screen.getByText("背景是你上傳的圖，不會重畫")).toBeInTheDocument();
  });

  it("new pages and drafts have no 編輯", () => {
    mount(entry({ pageId: null }));
    expect(screen.queryByRole("button", { name: "編輯" })).toBeNull();
  });
});

describe("StorySection 不放主角", () => {
  const entry = (n: number, o: Partial<PreviewEntry> = {}): PreviewEntry => ({
    n,
    key: `p${n}`,
    draft: null,
    pageId: null,
    scene: "恐龍站在畫面右邊，小小的",
    action: "揮手",
    captionZh: "",
    captionEn: "",
    materials: {
      hasBackground: false,
      backgroundUploaded: false,
      hasPose: false,
      redrawBackground: false,
      redrawPose: false,
    },
    autoCleanup: false,
    includeCharacter: true,
    characterLocked: false,
    ...o,
  });
  function mount(entries: PreviewEntry[], uploads = new Map<string, File>()) {
    const onCharacter = vi.fn();
    const onCharacterAll = vi.fn();
    render(
      <ToastProvider>
        <StorySection
          preview={{ entries, rows: [], pasted: 0, problems: [] }}
          text=""
          onText={vi.fn()}
          uploads={uploads}
          onUpload={vi.fn()}
          onAddPage={vi.fn()}
          onDraftChange={vi.fn()}
          onDraftRemove={vi.fn()}
          onSavePage={vi.fn()}
          onCharacter={onCharacter}
          onCharacterAll={onCharacterAll}
          existing={0}
          disabled={false}
        />
      </ToastProvider>,
    );
    return { onCharacter, onCharacterAll };
  }
  const master = () => screen.getAllByRole("checkbox", { name: "不放主角" })[0];
  const rowBox = (n: number) =>
    within(screen.getByRole("listitem", { name: `第 ${n} 頁` })).getByRole("checkbox", { name: "不放主角" });

  it("總開關勾起來每頁都勾（沒鎖的頁）", async () => {
    const { onCharacterAll } = mount([entry(1), entry(2)]);
    await userEvent.click(master());
    expect(onCharacterAll).toHaveBeenCalledWith(true);
  });

  it("全部都勾：總開關是勾選；取消回報 false", async () => {
    const { onCharacterAll } = mount([entry(1, { includeCharacter: false }), entry(2, { includeCharacter: false })]);
    expect(master()).toBeChecked();
    await userEvent.click(master());
    expect(onCharacterAll).toHaveBeenCalledWith(false);
  });

  it("個別取消後總開關是部分（indeterminate）", () => {
    mount([entry(1, { includeCharacter: false }), entry(2)]);
    expect(master()).not.toBeChecked();
    expect((master() as HTMLInputElement).indeterminate).toBe(true);
  });

  it("每列勾選回報這一頁", async () => {
    const { onCharacter } = mount([entry(1), entry(2)]);
    await userEvent.click(rowBox(2));
    expect(onCharacter).toHaveBeenCalledWith("p2", false);
  });

  it("會畫成跟著場景文字更新：勾了才顯示；上傳背景的列不顯示", () => {
    mount(
      [entry(1, { includeCharacter: false }), entry(2), entry(3, { includeCharacter: false })],
      new Map([["p3", new File(["x"], "bg.png", { type: "image/png" })]]),
    );
    expect(within(screen.getByRole("listitem", { name: "第 1 頁" })).getByText(`會畫成：${noCharacterScene("恐龍站在畫面右邊，小小的")}`)).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "第 2 頁" })).queryByText(/會畫成/)).toBeNull();
    expect(within(screen.getByRole("listitem", { name: "第 3 頁" })).queryByText(/會畫成/)).toBeNull();
  });

  it("相框頁勾選框停用並說明；總開關只看沒鎖的頁", () => {
    mount([entry(1, { characterLocked: true }), entry(2, { includeCharacter: false })]);
    expect(rowBox(1)).toBeDisabled();
    expect(rowBox(1)).not.toBeChecked();
    expect(screen.getByText("相框頁要放主角")).toBeInTheDocument();
    expect(master()).toBeChecked();
    expect((master() as HTMLInputElement).indeterminate).toBe(false);
  });
});
