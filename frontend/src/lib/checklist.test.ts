import { describe, expect, it } from "vitest";
import type { Page, QA } from "@/api/client";
import { checklist, type CheckItem } from "./checklist";

const page = (o: Partial<Page> = {}): Page =>
  ({
    id: 1,
    book_id: 1,
    position: 0,
    scene_prompt: "",
    selected_generation_id: null,
    background_generation_id: null,
    pose_generation_id: null,
    placement: null,
    status: "draft",
    line_detail: "standard",
    line_width_px: 11,
    character_check: null,
    lineart_url: null,
    needs_fix: null,
    aspect_mismatch: false,
    compose_pending: false,
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
    character_check: null,
    qa_note: "",
    forced_pass_reason: null,
    lineart_url: "/x.png",
    ...o,
  }) as QA;

const byKey = (items: CheckItem[]) => Object.fromEntries(items.map((i) => [i.key, i]));

describe("checklist", () => {
  it("checklist returns three items：構圖、補線、完成，順序固定", () => {
    const items = checklist(page(), undefined);
    expect(items.map((i) => i.key)).toEqual(["compose", "line", "passed"]);
    expect(items.map((i) => i.label)).toEqual(["構圖", "補線", "完成"]);
  });

  it("no background：構圖 todo「還沒選背景」；其他待辦", () => {
    const c = byKey(checklist(page(), undefined));
    expect(c.compose).toMatchObject({ state: "todo", note: "還沒選背景" });
    expect(c.line).toMatchObject({ state: "todo", note: "" });
    expect(c.passed).toMatchObject({ state: "todo", note: "" });
    expect(byKey(checklist(page({ pose_generation_id: 4 }), undefined)).compose.note).toBe("還沒選背景");
  });

  it("只有背景 → 構圖 todo「還沒選角色動作」", () => {
    expect(byKey(checklist(page({ background_generation_id: 3 }), undefined)).compose).toMatchObject({
      state: "todo",
      note: "還沒選角色動作",
    });
  });

  it("both selected with no composite → 構圖 todo、沒有註記；補線待辦", () => {
    const c = byKey(checklist(page({ background_generation_id: 3, pose_generation_id: 4 }), undefined));
    expect(c.compose).toMatchObject({ state: "todo", note: "" });
    expect(c.line).toMatchObject({ state: "todo", note: "" });
    // 還沒選圖時 compose_pending 不算（沒有東西要更新）
    const p = byKey(checklist(page({ background_generation_id: 3, pose_generation_id: 4, compose_pending: true }), undefined));
    expect(p.compose).toMatchObject({ state: "todo", note: "" });
  });

  it("composite with 3 gaps：構圖 done「已合成」；補線 warn「3 個缺口」", () => {
    const c = byKey(
      checklist(page({ background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 7 }), qa({ gap_count: 3 }), {
        selectedKind: "composite",
      }),
    );
    expect(c.compose).toMatchObject({ state: "done", note: "已合成" });
    expect(c.line).toMatchObject({ state: "warn", note: "3 個缺口" });
    expect(c.passed).toMatchObject({ state: "todo", note: "" });
  });

  it("沒有缺口 → 補線 done「線條都接好了」；QA 載入中 → 「檢查中…」", () => {
    const sel = page({ selected_generation_id: 7 });
    expect(byKey(checklist(sel, qa({ gap_count: 0 }))).line).toMatchObject({ state: "done", note: "線條都接好了" });
    expect(byKey(checklist(sel, undefined)).line).toMatchObject({ state: "todo", note: "檢查中…" });
  });

  it("已合成後改了位置或素材（compose_pending）→ 構圖 warn「待更新」；完成狀態不受影響", () => {
    const c = byKey(
      checklist(
        page({ background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 7, compose_pending: true, status: "passed" }),
        qa({ status: "passed" }),
        { selectedKind: "composite" },
      ),
    );
    expect(c.compose).toMatchObject({ state: "warn", note: "待更新" });
    expect(c.passed.state).toBe("done");
  });

  it("舊流程頁面（選定圖是 page）→ 構圖 done「舊流程圖片」；選了新素材還沒合成 → warn「待更新」", () => {
    const legacy = byKey(checklist(page({ selected_generation_id: 7 }), qa(), { selectedKind: "page" }));
    expect(legacy.compose).toMatchObject({ state: "done", note: "舊流程圖片" });
    expect(legacy.line.state).toBe("done");
    const redo = byKey(
      checklist(page({ background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 7, compose_pending: true }), qa(), {
        selectedKind: "page",
      }),
    );
    expect(redo.compose).toMatchObject({ state: "warn", note: "待更新" });
  });

  it("已選圖但種類未知（generations 載入中）→ 構圖 done「已合成」", () => {
    expect(byKey(checklist(page({ selected_generation_id: 7 }), undefined)).compose).toMatchObject({
      state: "done",
      note: "已合成",
    });
  });

  it("沒選圖時忽略 QA（舊快取）", () => {
    const c = byKey(checklist(page({ status: "passed" }), qa({ gap_count: 3, status: "passed" })));
    expect(c.line).toMatchObject({ state: "todo", note: "" });
    expect(c.passed.state).toBe("todo");
  });

  it("passed with a forced reason：done「完成（N 個缺口）」；一般通過「完成」", () => {
    const sel = { selected_generation_id: 7, status: "passed" as const };
    expect(byKey(checklist(page(sel), qa({ status: "passed" }))).passed).toMatchObject({ state: "done", note: "完成" });
    expect(
      byKey(checklist(page(sel), qa({ status: "passed", gap_count: 3, forced_pass_reason: "使用者確認完成（3 個缺口）" }))).passed,
    ).toMatchObject({ state: "done", note: "完成（3 個缺口）" });
  });

  it("QA 的 status 比頁面新（修線後退回草稿）", () => {
    const c = byKey(checklist(page({ selected_generation_id: 7, status: "passed" }), qa({ status: "draft" })));
    expect(c.passed.state).toBe("todo");
  });

  it("全部完成的頁", () => {
    const items = checklist(
      page({ background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 7, status: "passed" }),
      qa({ status: "passed" }),
      { selectedKind: "composite" },
    );
    expect(items.every((i) => i.state === "done")).toBe(true);
  });

  it("C 的狀態：請看一下（attention）→ 完成 warn「請看一下」；等著重新合成 → 完成 todo「製作中」（和 B 同樣的字）", () => {
    const att = byKey(
      checklist(page({ selected_generation_id: 7, status: "passed", attention: "failed" }), qa({ status: "passed" })),
    );
    expect(att.passed).toMatchObject({ state: "warn", note: "請看一下" });
    const working = byKey(
      checklist(
        page({ selected_generation_id: 7, status: "passed", recompose_pending: true }),
        qa({ status: "passed" }),
      ),
    );
    expect(working.passed).toMatchObject({ state: "todo", note: "製作中" });
    // 重新合成失敗：還等著，但流程沒在跑 → 和 B 一樣是請看一下
    const failed = byKey(
      checklist(
        page({ selected_generation_id: 7, status: "passed", recompose_pending: true, attention: "failed" }),
        qa({ status: "passed" }),
      ),
    );
    expect(failed.passed).toMatchObject({ state: "warn", note: "請看一下" });
  });

  it("軟標記 review_pose（換了新的主角動作）：通過的頁還是完成（done），只附上「請看一下」", () => {
    const review = byKey(
      checklist(
        page({ selected_generation_id: 7, status: "passed", attention: "review_pose" }),
        qa({ status: "passed" }),
      ),
    );
    expect(review.passed).toMatchObject({ state: "done", note: "完成・請看一下" });
    // 還沒通過：照一般的待辦，不是 warn
    const draft = byKey(
      checklist(page({ selected_generation_id: 7, status: "draft", attention: "review_pose" }), qa({ status: "draft" })),
    );
    expect(draft.passed).toMatchObject({ state: "todo", note: "" });
  });
});
