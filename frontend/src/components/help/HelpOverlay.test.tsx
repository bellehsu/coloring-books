import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/Dialog";
import { HelpOverlay } from "./HelpOverlay";

const TITLE = "現在的畫圖流程";
const help = () => screen.queryByRole("dialog", { name: TITLE });

let path = "/";
vi.mock("next/navigation", () => ({ usePathname: () => path }));
afterEach(() => {
  path = "/";
});

function Page({ children }: { children?: React.ReactNode }) {
  return (
    <>
      <button>頁面按鈕</button>
      <input aria-label="書名" />
      <textarea aria-label="故事" />
      <div contentEditable suppressContentEditableWarning aria-label="可編輯" role="textbox" />
      {children}
      <HelpOverlay />
    </>
  );
}

describe("按 h／H 叫出流程說明", () => {
  it("h 打開、再按 h 關閉，焦點回到原本的元素", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const btn = screen.getByRole("button", { name: "頁面按鈕" });
    btn.focus();
    await user.keyboard("h");
    expect(help()).toBeInTheDocument();
    await user.keyboard("h");
    expect(help()).toBeNull();
    expect(btn).toHaveFocus();
  });

  it("大寫 H 也可以；Esc 關閉", async () => {
    const user = userEvent.setup();
    render(<Page />);
    await user.keyboard("H");
    expect(help()).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(help()).toBeNull();
  });

  it("內容：三個步驟、三個 workflow、五個模型檔、關閉提示", async () => {
    const user = userEvent.setup();
    render(<Page />);
    await user.keyboard("h");
    const dlg = help()!;
    for (const step of ["① 主角參考圖", "② 畫背景與角色動作", "③ 變成著色頁"]) {
      expect(dlg).toHaveTextContent(step);
    }
    for (const wf of ["qwen_character", "qwen_background", "qwen_edit_pose_cutout"]) {
      expect(dlg).toHaveTextContent(wf);
    }
    for (const f of [
      "qwen-image-edit-2511-Q4_K_M.gguf",
      "qwen_2.5_vl_7b_fp8_scaled.safetensors",
      "qwen_image_vae.safetensors",
      "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
      "birefnet.safetensors",
    ]) {
      expect(dlg).toHaveTextContent(f);
    }
    expect(dlg).toHaveTextContent("按 h 或 Esc 關閉");
  });

  it.each([
    ["書名", "textbox", "書名"],
    ["故事", "textbox", "故事"],
  ])("在%s欄位打字不會打開", async (_n, role, name) => {
    const user = userEvent.setup();
    render(<Page />);
    const field = screen.getByRole(role, { name });
    await user.click(field);
    await user.keyboard("h");
    expect(help()).toBeNull();
    expect(field).toHaveValue("h");
  });

  it("在 contentEditable 裡不會打開", async () => {
    const user = userEvent.setup();
    render(<Page />);
    await user.click(screen.getByRole("textbox", { name: "可編輯" }));
    await user.keyboard("h");
    expect(help()).toBeNull();
  });

  it("搭配 Ctrl／⌘／Alt 不算", async () => {
    const user = userEvent.setup();
    render(<Page />);
    await user.keyboard("{Control>}h{/Control}");
    await user.keyboard("{Meta>}h{/Meta}");
    await user.keyboard("{Alt>}h{/Alt}");
    expect(help()).toBeNull();
  });

  it("別的對話框開著時不會疊上去", async () => {
    function WithDialog() {
      const [open, setOpen] = useState(true);
      return (
        <Page>
          <Dialog open={open} onClose={() => setOpen(false)} title="改書名">
            <button>確定</button>
          </Dialog>
        </Page>
      );
    }
    const user = userEvent.setup();
    render(<WithDialog />);
    await user.keyboard("h");
    expect(help()).toBeNull();
    expect(screen.getByRole("dialog", { name: "改書名" })).toBeInTheDocument();
  });

  it("還沒登入的「連結無效」頁不會打開", async () => {
    path = "/invalid-link";
    const user = userEvent.setup();
    render(<Page />);
    await user.keyboard("h");
    expect(help()).toBeNull();
  });
});
