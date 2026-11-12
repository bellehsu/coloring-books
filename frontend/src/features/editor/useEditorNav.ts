import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { Page } from "@/api/client";
import { allowedMode, DEFAULT_MODE, normalizeMode, type EditorMode, type ModeGate } from "./StageHeader";

interface Nav {
  page: number | null;
  mode: EditorMode;
}

function parseNav(sp: URLSearchParams): Nav {
  const n = Number(sp.get("page"));
  const m = sp.get("mode");
  return {
    page: Number.isInteger(n) && n > 0 ? n : null,
    mode: normalizeMode(m),
  };
}

/** 這頁能不能進 ② 補線的條件（modeBlockers／allowedMode 用）。 */
export const gateOf = (p: Page | null | undefined): ModeGate => ({
  selectedId: p?.selected_generation_id ?? null,
  recomposing: !!p?.recompose_pending,
  recomposeFailed: !!p?.recompose_pending && !!p.attention,
});

/**
 * 目前頁與模式：本機狀態為準（立即反應），寫回網址；網址被外部改變時跟著改。
 * 我們寫過、網址還沒追上的 query string（依序）。router.replace 可能晚到：較早的那幾個到了要忽略，
 * 否則連點 A、B 時晚到的 A 會把畫面切回 A。不在清單裡的變化才是外部導覽。
 */
export function useEditorNav(pages: Page[]) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const qs = sp.toString();
  const [nav, setNav] = useState<Nav>(() => parseNav(sp));
  const [seenQs, setSeenQs] = useState(qs);
  const [writes, setWrites] = useState<string[]>([]);
  if (qs !== seenQs) {
    setSeenQs(qs);
    const i = writes.indexOf(qs);
    if (i === -1) {
      setWrites([]);
      setNav(parseNav(sp));
    } else {
      setWrites(writes.slice(i + 1));
    }
  }

  const current = pages.find((p) => p.id === nav.page) ?? pages[0] ?? null;
  const mode: EditorMode = allowedMode(nav.mode, gateOf(current));

  const go = (page: number, m: EditorMode) => {
    setNav({ page, mode: m });
    const q = new URLSearchParams();
    q.set("page", String(page));
    if (m !== DEFAULT_MODE) q.set("mode", m);
    const next = q.toString();
    if (next !== qs) setWrites((w) => [...w, next]);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  /** 換頁：目標頁不能用目前的模式就回到構圖（否則之後條件成立時會突然跳過去）。 */
  const selectPage = (id: number) => {
    const target = pages.find((p) => p.id === id);
    go(id, allowedMode(mode, gateOf(target)));
  };

  return { current, mode, go, selectPage };
}
