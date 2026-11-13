import { afterEach, describe, expect, it, vi } from "vitest";
import { isFreshPose, lastSeenPose, markSeenPose, readSeenPose } from "./seenPoses";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("seenPoses：每頁看過的最新角色動作（瀏覽器的 localStorage）", () => {
  it("round-trips the high-water mark per page", () => {
    expect(lastSeenPose(5)).toBeNull();
    markSeenPose(5, 21);
    markSeenPose(6, 40);
    expect(lastSeenPose(5)).toBe(21);
    expect(lastSeenPose(6)).toBe(40);
    expect(localStorage.getItem("cb:seenPose:5")).toBe("21");
    // 只往上走：比較舊的不會蓋掉
    markSeenPose(5, 12);
    expect(lastSeenPose(5)).toBe(21);
    markSeenPose(5, 30);
    expect(lastSeenPose(5)).toBe(30);
  });

  it("ignores a stored value that is not a number", () => {
    localStorage.setItem("cb:seenPose:5", "oops");
    expect(lastSeenPose(5)).toBeNull();
  });

  it("returns null and does not throw when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    expect(lastSeenPose(5)).toBeNull();
    expect(() => markSeenPose(5, 21)).not.toThrow();
  });

  it("returns null and does not throw when touching localStorage itself throws", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(lastSeenPose(5)).toBeNull();
    expect(() => markSeenPose(5, 21)).not.toThrow();
  });
});

describe("isFreshPose：哪張標「新」", () => {
  it("with a mark: newer than the mark (today's rule, the current one included)", () => {
    const seen = { ok: true as const, id: 20 };
    expect(isFreshPose(22, seen, 20, 22)).toBe(true);
    expect(isFreshPose(20, seen, 20, 22)).toBe(false);
    expect(isFreshPose(18, seen, 20, 22)).toBe(false);
  });

  it("first visit (no mark): newer than the pose in use at open, and not the current one", () => {
    const seen = { ok: true as const, id: null };
    expect(isFreshPose(23, seen, 22, 22)).toBe(true);
    expect(isFreshPose(22, seen, 22, 22)).toBe(false);
    expect(isFreshPose(20, seen, 22, 22)).toBe(false);
    // 打開之後換成 23：23 是使用中，不標
    expect(isFreshPose(23, seen, 22, 23)).toBe(false);
    // 沒有使用中的：不標
    expect(isFreshPose(23, seen, null, null)).toBe(false);
  });

  it("localStorage unreadable: never fresh", () => {
    expect(isFreshPose(23, { ok: false }, 20, 20)).toBe(false);
  });

  it("readSeenPose tells no-mark from unreadable", () => {
    expect(readSeenPose(5)).toEqual({ ok: true, id: null });
    markSeenPose(5, 21);
    expect(readSeenPose(5)).toEqual({ ok: true, id: 21 });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(readSeenPose(5)).toEqual({ ok: false });
  });
});
