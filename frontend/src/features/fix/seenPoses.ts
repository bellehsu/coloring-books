/**
 * C 的「新」標記（spec §4.4、§6）：每頁在這個瀏覽器看過的最新角色動作 id（localStorage 的高水位）。
 * 存取 localStorage 可能丟例外（無痕視窗、停用網站資料、空間滿了）：一律吞掉，讀不到就當沒看過（null）。
 */
const key = (pageId: number) => `cb:seenPose:${pageId}`;

/** 讀這頁的標記：沒記過是 `{ ok: true, id: null }`；localStorage 讀不到是 `{ ok: false }`（不標「新」）。 */
export function readSeenPose(pageId: number): { ok: true; id: number | null } | { ok: false } {
  try {
    const raw = window.localStorage.getItem(key(pageId));
    const n = raw ? Number(raw) : NaN;
    return { ok: true, id: Number.isInteger(n) ? n : null };
  } catch {
    return { ok: false };
  }
}

/** 這頁上次看過的最新角色動作 id；沒記過或讀不到回 null。 */
export function lastSeenPose(pageId: number): number | null {
  const r = readSeenPose(pageId);
  return r.ok ? r.id : null;
}

/**
 * 這張要不要標「新」。有標記：比上次看過的新。還沒有標記（第一次在這個瀏覽器打開這頁，例如批次重畫之後）：
 * 比打開時使用中的那張新、而且不是現在使用中的。localStorage 讀不到：一律不標（標記也存不了，不然每次都是「新」）。
 * @param seen 打開這頁時讀到的標記（readSeenPose）
 * @param baseline 打開這頁時使用中的角色動作 id
 * @param current 現在使用中的角色動作 id
 */
export function isFreshPose(
  id: number,
  seen: ReturnType<typeof readSeenPose>,
  baseline: number | null,
  current: number | null,
): boolean {
  if (!seen.ok) return false;
  if (seen.id !== null) return id > seen.id;
  return baseline !== null && id > baseline && id !== current;
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
