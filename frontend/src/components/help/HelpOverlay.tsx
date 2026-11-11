"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { FlowHelp } from "./FlowHelp";

/** 還沒登入（邀請連結無效）的頁面不提供說明。 */
const NO_HELP_PATHS = new Set(["/invalid-link"]);

/** 焦點在可以打字的地方（輸入框、文字區、下拉選單、contentEditable）：h 是在打字，不是快捷鍵。 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
    return true;
  }
  return target.closest('[contenteditable]:not([contenteditable="false"])') !== null;
}

/**
 * 任何頁面按 h／H 叫出「現在的畫圖流程」說明（再按 h 或 Esc 關閉）。
 * 打字中、搭配 Ctrl／⌘／Alt、輸入法組字中、或別的對話框開著時不觸發。
 */
export function HelpOverlay() {
  const pathname = usePathname();
  const enabled = !NO_HELP_PATHS.has(pathname ?? "");
  const [open, setOpen] = useState(false);
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  });

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "h" && e.key !== "H") return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing || e.repeat || e.defaultPrevented) return;
      if (isTypingTarget(e.target)) return;
      if (!openRef.current && document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      setOpen((o) => !o);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);

  return (
    <Dialog open={open && enabled} onClose={() => setOpen(false)} title="現在的畫圖流程" size="wide">
      <FlowHelp />
    </Dialog>
  );
}
