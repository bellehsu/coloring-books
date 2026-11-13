import { describe, expect, it } from "vitest";
import type { Page } from "@/api/client";
import { hardAttention, recomposeRunning } from "./pageState";

const pg = (o: Partial<Page>) => ({ recompose_pending: false, attention: null, ...o }) as Page;

describe("hardAttention", () => {
  it("is true for real problems, false for none and the soft review_pose tag", () => {
    expect(hardAttention({ attention: "failed" })).toBe(true);
    expect(hardAttention({ attention: null })).toBe(false);
    expect(hardAttention({})).toBe(false);
    expect(hardAttention({ attention: "review_pose" })).toBe(false);
  });
});

describe("recomposeRunning", () => {
  it("is still running for a recompose_pending page tagged review_pose", () => {
    expect(recomposeRunning(pg({ recompose_pending: true, attention: "review_pose" }))).toBe(true);
  });
  it("stops on a real failure", () => {
    expect(recomposeRunning(pg({ recompose_pending: true, attention: "failed" }))).toBe(false);
    expect(recomposeRunning(pg({ recompose_pending: false }))).toBe(false);
    expect(recomposeRunning(undefined)).toBe(false);
  });
});
