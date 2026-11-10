import type { Book } from "@/api/client";

export interface NextStep {
  text: string;
  tone: "ok" | "warn" | "draft" | "busy";
}

export function nextStep(book: Book): NextStep {
  const { pages, passed, needs_fix } = book.stats;
  if (book.character_generation_id == null) return { text: "下一步：設定角色", tone: "draft" };
  if (pages === 0) return { text: "下一步：新增頁面", tone: "draft" };
  if (needs_fix > 0) return { text: `下一步：${needs_fix} 頁需要修線`, tone: "warn" };
  if (passed < pages) return { text: `下一步：${pages - passed} 頁待完成`, tone: "busy" };
  return { text: "可以匯出", tone: "ok" };
}
