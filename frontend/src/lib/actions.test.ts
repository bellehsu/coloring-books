import { describe, expect, it } from "vitest";
import { ACTIONS, hasPhrase, togglePhrase } from "./actions";

describe("actions", () => {
  it("has the ten presets", () => {
    expect(ACTIONS.map((a) => a.label)).toEqual(["站著", "坐著", "走路", "跑步", "揮手", "指向前方", "拿著東西", "跳起來", "看書", "開心笑"]);
    expect(ACTIONS[4].phrase).toBe("waving one hand");
    expect(ACTIONS[6].phrase).toBe("holding something with both hands");
  });
  it("appends with a comma and does not duplicate", () => {
    expect(togglePhrase("", "standing")).toBe("standing");
    expect(togglePhrase("waving one hand", "reading a book")).toBe("waving one hand, reading a book");
    expect(togglePhrase("waving one hand, reading a book", "waving one hand")).toBe("waving one hand, reading a book");
  });
  it("detects phrases as whole comma-separated items", () => {
    expect(hasPhrase("sitting, waving one hand", "waving one hand")).toBe(true);
    expect(hasPhrase("a standing man", "standing")).toBe(false);
  });
});
