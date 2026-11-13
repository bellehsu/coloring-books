/**
 * 複製文字：安全環境（https／localhost）先用 Clipboard API；
 * 不行（http 網址、權限被擋）就退回隱藏 textarea＋execCommand("copy")。回傳有沒有複製成功。
 */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* 退回 execCommand */
    }
  }
  const prev = document.activeElement as HTMLElement | null;
  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.setAttribute("aria-hidden", "true");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
  document.body.appendChild(area);
  try {
    area.focus();
    area.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
    if (prev && prev.isConnected) prev.focus();
  }
}
