import { describe, expect, it } from "vitest";
import { ACTIONS, hasPhrase, appendPhrase } from "./actions";

describe("actions", () => {
  it("has the ten presets", () => {
    expect(ACTIONS.map((a) => a.label)).toEqual(["站著", "坐著", "走路", "跑步", "揮手", "指向前方", "拿著東西", "跳起來", "看書", "開心笑"]);
    expect(ACTIONS[4].phrase).toBe("waving one hand");
    expect(ACTIONS[6].phrase).toBe("holding something with both hands");
    // 校準：單獨的 "sitting" 不可靠
    expect(ACTIONS[1].phrase).toBe("sitting on the ground");
  });
  it("plain 'sitting' does not count as the 坐著 preset being added", () => {
    const sit = ACTIONS[1].phrase;
    expect(hasPhrase("sitting, waving one hand", sit)).toBe(false);
    expect(hasPhrase("Sitting on the ground and smiling", sit)).toBe(true);
    expect(appendPhrase("sitting", sit)).toBe("sitting, sitting on the ground");
  });
  it("appends with a comma and does not duplicate", () => {
    expect(appendPhrase("", "standing")).toBe("standing");
    expect(appendPhrase("waving one hand", "reading a book")).toBe("waving one hand, reading a book");
    expect(appendPhrase("waving one hand, reading a book", "waving one hand")).toBe("waving one hand, reading a book");
  });
  it("detects phrases as whole words, case-insensitively, anywhere in the text", () => {
    expect(hasPhrase("sitting, waving one hand", "waving one hand")).toBe(true);
    expect(hasPhrase("Waving one hand", "waving one hand")).toBe(true);
    expect(hasPhrase("waving one hand and smiling", "waving one hand")).toBe(true);
    expect(hasPhrase("outstanding", "standing")).toBe(false);
    expect(appendPhrase("Waving one hand", "waving one hand")).toBe("Waving one hand");
  });
});
