import { describe, expect, it } from "vitest";
import { fixHref, parseFilter } from "./fixHref";

describe("fixHref", () => {
  it("parseFilter and fixHref keep only attention", () => {
    expect(parseFilter("attention")).toBe("attention");
    expect(parseFilter("ready")).toBeNull();
    expect(parseFilter(null)).toBeNull();
    expect(fixHref(1, 5)).toBe("/books/1/page/5");
    expect(fixHref(1, 5, "attention")).toBe("/books/1/page/5?filter=attention");
  });
});
