import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";
import { Drawer } from "./Drawer";

function Host({ Comp }: { Comp: typeof Dialog | typeof Drawer }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>開啟</button>
      <Comp open={open} onClose={() => setOpen(false)} title="標題">
        <input aria-label="欄位" />
        <button>確定</button>
      </Comp>
    </>
  );
}

describe.each([
  ["Dialog", Dialog],
  ["Drawer", Drawer],
])("%s", (_n, Comp) => {
  it("有 role=dialog 與 aria-labelledby，焦點進入第一個可聚焦元素", async () => {
    const user = userEvent.setup();
    render(<Host Comp={Comp} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "開啟" }));
    const dlg = screen.getByRole("dialog", { name: "標題" });
    expect(dlg).toHaveAttribute("aria-modal", "true");
    // 第一個可聚焦元素是右上角關閉鈕
    expect(screen.getByRole("button", { name: "關閉" })).toHaveFocus();
  });

  it("Esc 關閉並把焦點還給開啟的按鈕", async () => {
    const user = userEvent.setup();
    render(<Host Comp={Comp} />);
    const opener = screen.getByRole("button", { name: "開啟" });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("點背景關閉；點內容不關閉", async () => {
    const user = userEvent.setup();
    render(<Host Comp={Comp} />);
    await user.click(screen.getByRole("button", { name: "開啟" }));
    await user.click(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByTestId("overlay"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Tab 在對話框內循環", async () => {
    const user = userEvent.setup();
    render(<Host Comp={Comp} />);
    await user.click(screen.getByRole("button", { name: "開啟" }));
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "確定" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "關閉" })).toHaveFocus();
  });

  it("onClose 被呼叫", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Comp open onClose={onClose} title="T">
        x
      </Comp>,
    );
    await user.click(screen.getByRole("button", { name: "關閉" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Modal 進階行為", () => {
  it("點到不可聚焦文字後 Esc 仍可關閉", async () => {
    const user = userEvent.setup();
    render(
      <Host2 />,
    );
    await user.click(screen.getByRole("button", { name: "開啟" }));
    await user.click(screen.getByText("說明文字"));
    expect(document.body).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("巢狀：Esc 只關最上層；Tab 只在最上層循環；捲動鎖定到最後一個關閉", async () => {
    const user = userEvent.setup();
    render(<Nested />);
    await user.click(screen.getByRole("button", { name: "開抽屜" }));
    expect(document.body.style.overflow).toBe("hidden");
    await user.click(screen.getByRole("button", { name: "開確認" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    const confirm = screen.getByRole("dialog", { name: "確認" });
    await user.tab();
    await user.tab();
    await user.tab();
    expect(confirm).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "確認" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "抽屜" })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });

  it("在面板內按下、在背景放開不關閉", async () => {
    const user = userEvent.setup();
    render(<Host2 />);
    await user.click(screen.getByRole("button", { name: "開啟" }));
    const overlay = screen.getByTestId("overlay");
    await user.pointer([
      { keys: "[MouseLeft>]", target: screen.getByText("說明文字") },
      { keys: "[/MouseLeft]", target: overlay },
    ]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

function Host2() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>開啟</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="標題">
        <p>說明文字</p>
      </Dialog>
    </>
  );
}

function Nested() {
  const [d, setD] = useState(false);
  const [c, setC] = useState(false);
  return (
    <>
      <button onClick={() => setD(true)}>開抽屜</button>
      <Drawer open={d} onClose={() => setD(false)} title="抽屜">
        <button onClick={() => setC(true)}>開確認</button>
        <Dialog open={c} onClose={() => setC(false)} title="確認">
          <button>是</button>
        </Dialog>
      </Drawer>
    </>
  );
}
