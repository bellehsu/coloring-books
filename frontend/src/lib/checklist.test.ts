import { describe, expect, it } from "vitest";
import type { Page, QA } from "@/api/client";
import { checklist, type CheckItem } from "./checklist";

const page = (o: Partial<Page> = {}): Page =>
  ({
    id: 1,
    book_id: 1,
    position: 0,
    scene_prompt: "",
    controlnet_strength: 0.4,
    sketch_url: null,
    selected_generation_id: null,
    status: "draft",
    line_detail: "standard",
    line_width_px: 11,
    character_check: null,
    lineart_url: null,
    needs_fix: null,
    aspect_mismatch: false,
    created_at: "",
    updated_at: "",
    ...o,
  }) as Page;

const qa = (o: Partial<QA> = {}): QA =>
  ({
    status: "draft",
    errors: [],
    warnings: [],
    gaps: [],
    gap_count: 0,
    dangling_count: 0,
    near_miss_count: 0,
    metrics: {},
    character_check: null,
    qa_note: "",
    forced_pass_reason: null,
    lineart_url: "/x.png",
    ...o,
  }) as QA;

const byKey = (items: CheckItem[]) => Object.fromEntries(items.map((i) => [i.key, i]));

describe("checklist", () => {
  it("一律 5 項、順序固定", () => {
    const items = checklist(page(), undefined);
    expect(items.map((i) => i.key)).toEqual(["sketch", "selected", "closed", "character", "passed"]);
    expect(items.map((i) => i.label)).toEqual(["構圖草圖", "選定圖片", "線條封閉", "角色外型", "通過 QA"]);
  });

  it("全新頁：沒草圖是 skip（灰色、非錯誤），其餘待辦", () => {
    const c = byKey(checklist(page(), undefined));
    expect(c.sketch).toMatchObject({ state: "skip", note: "未畫（不使用構圖）" });
    expect(c.selected).toMatchObject({ state: "todo", note: "還沒選圖" });
    expect(c.closed.state).toBe("todo");
    expect(c.character.state).toBe("todo");
    expect(c.passed).toMatchObject({ state: "todo", note: "" });
  });

  it("有草圖 → done「已畫」", () => {
    expect(byKey(checklist(page({ sketch_url: "/s.png?v=1" }), undefined)).sketch).toMatchObject({
      state: "done",
      note: "已畫",
    });
  });

  it("已選圖：顯示 seed；QA 載入中 → 線條封閉為 todo「檢查中…」", () => {
    const c = byKey(checklist(page({ selected_generation_id: 7 }), undefined, { seed: 101 }));
    expect(c.selected).toMatchObject({ state: "done", note: "seed 101" });
    expect(c.closed).toMatchObject({ state: "todo", note: "檢查中…" });
    expect(c.character).toMatchObject({ state: "todo", note: "待確認" });
  });

  it("已選圖但不知 seed → note「已選」", () => {
    expect(byKey(checklist(page({ selected_generation_id: 7 }), qa())).selected.note).toBe("已選");
  });

  it("有缺口 → warn「N 個缺口」；沒缺口 → done", () => {
    const sel = page({ selected_generation_id: 7 });
    expect(byKey(checklist(sel, qa({ gap_count: 3 }))).closed).toMatchObject({ state: "warn", note: "3 個缺口" });
    expect(byKey(checklist(sel, qa({ gap_count: 0 }))).closed).toMatchObject({ state: "done", note: "0 個缺口" });
  });

  it("沒選圖時忽略 QA（舊快取）", () => {
    const c = byKey(checklist(page(), qa({ gap_count: 3, character_check: "consistent" })));
    expect(c.closed.state).toBe("todo");
    expect(c.character.state).toBe("todo");
  });

  it("角色外型：一致 done、不一致 warn、未確認 todo；QA 的值優先於頁面", () => {
    const sel = page({ selected_generation_id: 7 });
    expect(byKey(checklist(sel, qa({ character_check: "consistent" }))).character).toMatchObject({
      state: "done",
      note: "一致",
    });
    expect(byKey(checklist(sel, qa({ character_check: "inconsistent" }))).character).toMatchObject({
      state: "warn",
      note: "不一致",
    });
    expect(byKey(checklist(sel, qa())).character).toMatchObject({ state: "todo", note: "待確認" });
    expect(
      byKey(checklist(page({ selected_generation_id: 7, character_check: "consistent" }), undefined)).character.state,
    ).toBe("done");
    expect(
      byKey(checklist(page({ selected_generation_id: 7, character_check: "consistent" }), qa({ character_check: "inconsistent" })))
        .character.state,
    ).toBe("warn");
  });

  it("通過：done「完成」；強制通過 note「強制通過」", () => {
    const sel = { selected_generation_id: 7, status: "passed" as const };
    expect(byKey(checklist(page(sel), qa({ status: "passed" }))).passed).toMatchObject({ state: "done", note: "完成" });
    expect(
      byKey(checklist(page(sel), qa({ status: "passed", gap_count: 2, forced_pass_reason: "印刷時再修" }))).passed,
    ).toMatchObject({ state: "done", note: "強制通過" });
  });

  it("QA 的 status 比頁面新（修線後退回草稿）", () => {
    const c = byKey(checklist(page({ selected_generation_id: 7, status: "passed" }), qa({ status: "draft" })));
    expect(c.passed.state).toBe("todo");
  });

  it("全部完成的頁", () => {
    const items = checklist(
      page({ sketch_url: "/s.png", selected_generation_id: 7, status: "passed", character_check: "consistent" }),
      qa({ status: "passed", character_check: "consistent" }),
      { seed: 5 },
    );
    expect(items.every((i) => i.state === "done")).toBe(true);
  });
});
