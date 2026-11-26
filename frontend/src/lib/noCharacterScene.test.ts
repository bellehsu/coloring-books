import { describe, expect, it } from "vitest";
import cases from "./noCharacterScene.cases.json";
import { noCharacterScene } from "./noCharacterScene";

describe("noCharacterScene", () => {
  it("matches the shared cases", () => {
    expect(cases.length).toBeGreaterThanOrEqual(8);
    for (const c of cases) expect(noCharacterScene(c.in)).toBe(c.out);
  });
});
