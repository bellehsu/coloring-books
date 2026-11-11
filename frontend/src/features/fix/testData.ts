import type { OverviewPage, Page } from "@/api/client";

/** C 的測試用頁面（預設：第 2 頁，已合成、可以印，背景與角色動作都是自動畫的那張）。 */
export function mkPage(extra: Partial<Page> = {}): Page {
  return {
    id: 5,
    book_id: 1,
    position: 1,
    scene_prompt: "消防局前面",
    action_prompt: "開著消防車揮手",
    caption_zh: "消防車",
    caption_en: "Fire Engine",
    selected_generation_id: 30,
    background_generation_id: 10,
    pose_generation_id: 20,
    placement: null,
    status: "passed",
    line_detail: "standard",
    line_width_px: 11,
    character_check: null,
    lineart_url: "/api/pages/5/lineart?v=k1",
    needs_fix: false,
    aspect_mismatch: false,
    compose_pending: false,
    attention: null,
    attention_detail: null,
    auto_background_generation_id: 10,
    auto_pose_generation_id: 20,
    has_user_strokes: false,
    recompose_pending: false,
    can_undo: false,
    can_redo: false,
    created_at: "2026-11-12T01:00:00Z",
    updated_at: "2026-11-12T01:00:00Z",
    ...extra,
  };
}

export function ovPage(id: number, position: number, state: OverviewPage["state"]): OverviewPage {
  return {
    id,
    position,
    state,
    gap_count: null,
    caption_zh: null,
    caption_en: null,
    thumb_url: null,
    lineart_url: null,
    attention_detail: null,
  };
}
