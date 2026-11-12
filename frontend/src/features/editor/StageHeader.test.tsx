import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { allowedMode, DEFAULT_MODE, modeBlockers, normalizeMode, StageHeader, type EditorMode } from "./StageHeader";

function setup(mode: EditorMode = "compose", blockers: Partial<Record<EditorMode, string>> = {}, gapCount = 0) {
  const onModeChange = vi.fn();
  render(
    <StageHeader mode={mode} onModeChange={onModeChange} blockers={blockers} gapCount={gapCount} idPrefix="x" panelId="x-stage" />,
  );
  return onModeChange;
}

describe("StageHeader", () => {
  it("StageHeader renders exactly two tabs ① 構圖 and ② 補線", () => {
    setup();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["① 構圖", "② 補線"]);
    expect(screen.getByRole("tab", { name: "① 構圖" })).toHaveAttribute("aria-selected", "true");
  });

  it("停用的 ② 補線：說明原因、點了不切換", async () => {
    const onModeChange = setup("compose", { line: "請先擺好角色位置" });
    const line = screen.getByRole("tab", { name: "② 補線" });
    expect(line).toHaveAttribute("aria-disabled", "true");
    expect(line).toHaveAccessibleDescription("請先擺好角色位置");
    await userEvent.click(line);
    expect(onModeChange).not.toHaveBeenCalled();
  });

  it("② 補線分頁顯示缺口數；方向鍵切換分頁", async () => {
    const onModeChange = setup("compose", {}, 3);
    expect(screen.getByRole("tab", { name: /② 補線/ })).toHaveTextContent("3 個缺口");
    screen.getByRole("tab", { name: "① 構圖" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(onModeChange).toHaveBeenCalledWith("line");
  });
});

describe("modes", () => {
  it("normalizeMode maps background/pose→compose and qa→line", () => {
    expect(normalizeMode("background")).toBe("compose");
    expect(normalizeMode("pose")).toBe("compose");
    expect(normalizeMode("compose")).toBe("compose");
    expect(normalizeMode("qa")).toBe("line");
    expect(normalizeMode("line")).toBe("line");
    expect(normalizeMode(null)).toBe("compose");
    expect(normalizeMode("whatever")).toBe("compose");
    expect(DEFAULT_MODE).toBe("compose");
  });

  it("modeBlockers：沒有選定圖時 ② 補線停用「請先擺好角色位置」；① 構圖永遠可用", () => {
    expect(modeBlockers({ selectedId: null })).toEqual({ line: "請先擺好角色位置" });
    expect(modeBlockers({ selectedId: 5 })).toEqual({});
    expect(allowedMode("line", { selectedId: null })).toBe("compose");
    expect(allowedMode("line", { selectedId: 5 })).toBe("line");
    expect(allowedMode("compose", { selectedId: null })).toBe("compose");
  });

  it("modeBlockers：等著重新合成（C 改過）時 ② 補線停用並說明", () => {
    expect(modeBlockers({ selectedId: 5, recomposing: true })).toEqual({ line: "這一頁正在重新合成…好了會自動更新" });
    expect(allowedMode("line", { selectedId: 5, recomposing: true })).toBe("compose");
  });
});
