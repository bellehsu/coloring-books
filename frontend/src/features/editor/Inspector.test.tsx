import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CheckItem } from "@/lib/checklist";
import { Inspector } from "./Inspector";

const items: CheckItem[] = [{ key: "compose", label: "構圖", state: "todo", note: "" }];

/** 「本頁進度」與面板在 DOM 裡的先後（閱讀順序跟畫面一致，WCAG 1.3.2）。 */
const order = () => {
  const aside = screen.getByRole("complementary", { name: "檢查與設定" });
  return [...aside.children].map((el) => el.getAttribute("aria-label") ?? el.textContent);
};

describe("Inspector", () => {
  it("預設本頁進度在最上面，面板在下", () => {
    render(
      <Inspector title="第 1 頁進度" items={items}>
        <div>面板</div>
      </Inspector>,
    );
    expect(order()).toEqual(["本頁進度", "面板"]);
  });

  it("checklistLast（手機）：DOM 裡面板在前、本頁進度在最後（不靠 CSS order）", () => {
    render(
      <Inspector title="第 1 頁進度" items={items} checklistLast>
        <div>面板</div>
      </Inspector>,
    );
    expect(order()).toEqual(["面板", "本頁進度"]);
  });
});
