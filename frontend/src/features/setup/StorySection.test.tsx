import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { AI_PROMPT } from "./aiPrompt";
import { StorySection } from "./StorySection";
import type { StoryPreview } from "./storyPreview";

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
