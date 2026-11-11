import type { Book } from "@/api/client";

export interface NextStep {
  text: string;
  tone: "ok" | "warn" | "draft" | "busy";
}

/**
 * 首頁書卡的一句話，用 B 整本書的狀態（book.stats 的 ready／attention／working／todo，與 overview 同一套）：
 * 製作中＞請看一下＞還沒做完＞整本做好了。線沒接起來只是提示（可以不管），不算要處理的事，所以不看 needs_fix。
 */
export function nextStep(book: Book): NextStep {
  const { pages, attention, working, todo } = book.stats;
  if (book.character_generation_id == null) return { text: "下一步：先設定主角", tone: "draft" };
  if (pages === 0) return { text: "還沒開始做", tone: "draft" };
  if (working > 0) return { text: "製作中…", tone: "busy" };
  if (attention > 0) return { text: `${attention} 頁請你看一下`, tone: "warn" };
  if (todo > 0) return { text: `還有 ${todo} 頁還沒做完`, tone: "busy" };
  return { text: "整本做好了，可以印", tone: "ok" };
}
