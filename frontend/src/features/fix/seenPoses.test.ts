import { afterEach, describe, expect, it, vi } from "vitest";
import { lastSeenPose, markSeenPose } from "./seenPoses";

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
