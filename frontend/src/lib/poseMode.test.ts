import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POSE_MODE_KEY, effectiveMode, readPoseMode, writePoseMode } from "./poseMode";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("poseMode storage", () => {
  it("defaults to precise", () => {
    expect(readPoseMode()).toBe("precise");
  });

  it("remembers fast under coloring.poseMode", () => {
    writePoseMode("fast");
    expect(POSE_MODE_KEY).toBe("coloring.poseMode");
    expect(localStorage.getItem("coloring.poseMode")).toBe("fast");
    expect(readPoseMode()).toBe("fast");
    writePoseMode("precise");
    expect(readPoseMode()).toBe("precise");
  });

  it("an unknown stored value (e.g. qwen) reads as precise", () => {
    localStorage.setItem("coloring.poseMode", "qwen");
    expect(readPoseMode()).toBe("precise");
  });

  it("storage that throws reads as precise and writing does not throw", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(readPoseMode()).toBe("precise");
    expect(() => writePoseMode("fast")).not.toThrow();
  });
});

describe("effectiveMode", () => {
  it("availability not loaded yet → the stored mode", () => {
    expect(effectiveMode("precise", undefined)).toBe("precise");
    expect(effectiveMode("fast", undefined)).toBe("fast");
  });

  it("precise unavailable and fast available → fast", () => {
    expect(effectiveMode("precise", { precise: false, fast: true })).toBe("fast");
  });

  it("otherwise the stored mode", () => {
    expect(effectiveMode("precise", { precise: true, fast: true })).toBe("precise");
    expect(effectiveMode("fast", { precise: true, fast: true })).toBe("fast");
    expect(effectiveMode("precise", { precise: false, fast: false })).toBe("precise");
    expect(effectiveMode("fast", { precise: true, fast: false })).toBe("fast");
  });
});
