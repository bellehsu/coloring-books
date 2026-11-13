/**
 * C 的「新」標記（spec §4.4、§6）：每頁在這個瀏覽器看過的最新角色動作 id（localStorage 的高水位）。
 * 存取 localStorage 可能丟例外（無痕視窗、停用網站資料、空間滿了）：一律吞掉，讀不到就當沒看過（null）。
 */
const key = (pageId: number) => `cb:seenPose:${pageId}`;

/** 這頁上次看過的最新角色動作 id；沒記過或讀不到回 null。 */
export function lastSeenPose(pageId: number): number | null {
  try {
    const raw = window.localStorage.getItem(key(pageId));
    const n = raw ? Number(raw) : NaN;
    return Number.isInteger(n) ? n : null;
  } catch {
    return null;
  }
}

/** 記下這頁看過的最新角色動作 id（只往上走）。 */
export function markSeenPose(pageId: number, maxId: number): void {
  try {
    const prev = lastSeenPose(pageId);
    if (prev !== null && prev >= maxId) return;
    window.localStorage.setItem(key(pageId), String(maxId));
  } catch {
    // 存不了就算了：下次打開不會有「新」
  }
}
