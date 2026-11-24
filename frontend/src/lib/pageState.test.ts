import { describe, expect, it } from "vitest";
import type { Page } from "@/api/client";
import { hardAttention, pageDone, recomposeRunning, softAttention } from "./pageState";

const pg = (o: Partial<Page>) => ({ recompose_pending: false, attention: null, ...o }) as Page;

describe("hardAttention", () => {
  it("is true for real problems, false for none and the soft review_pose tag", () => {
    expect(hardAttention({ attention: "failed" })).toBe(true);
    expect(hardAttention({ attention: null })).toBe(false);
    expect(hardAttention({})).toBe(false);
    expect(hardAttention({ attention: "review_pose" })).toBe(false);
  });
  it("treats review_frame (portrait background without a frame) as soft too", () => {
    expect(hardAttention({ attention: "review_frame" })).toBe(false);
  });
});

describe("softAttention", () => {
  it("is true only for the soft tags review_pose and review_frame", () => {
    expect(softAttention({ attention: "review_pose" })).toBe(true);
    expect(softAttention({ attention: "review_frame" })).toBe(true);
    expect(softAttention({ attention: "failed" })).toBe(false);
    expect(softAttention({ attention: null })).toBe(false);
    expect(softAttention({})).toBe(false);
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

describe("pageDone", () => {
  it("counts passed pages without a real problem, including the soft review_pose tag", () => {
    expect(pageDone(pg({ status: "passed" }))).toBe(true);
    expect(pageDone(pg({ status: "passed", attention: "review_pose" }))).toBe(true);
    expect(pageDone(pg({ status: "passed", attention: "review_frame" }))).toBe(true);
  });
  it("does not count drafts, real problems or pages still recomposing", () => {
    expect(pageDone(pg({ status: "draft" }))).toBe(false);
    expect(pageDone(pg({ status: "passed", attention: "failed" }))).toBe(false);
    expect(pageDone(pg({ status: "passed", recompose_pending: true }))).toBe(false);
  });
});
