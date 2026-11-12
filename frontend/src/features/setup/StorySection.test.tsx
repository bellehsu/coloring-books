import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
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
