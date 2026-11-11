import { describe, expect, it } from "vitest";
import { ACTIONS, EXPRESSIONS, FACINGS, hasPhrase, appendPhrase, setExclusivePhrase } from "./actions";

describe("actions", () => {
  it("has the 17 presets", () => {
    expect(ACTIONS.map((a) => a.label)).toEqual([
      "站著", "坐著", "走路", "跑步", "揮手", "指向前方", "拿著東西", "跳起來", "看書",
      "拍手", "抱大球", "踢球", "彎腰撿東西", "伸懶腰", "跳舞", "吃蘋果", "躺著睡覺",
    ]);
    expect(ACTIONS.map((a) => a.phrase).slice(9)).toEqual([
      "clapping hands", "hugging a big ball", "kicking a ball", "bending down to pick something up",
      "stretching both arms up high", "dancing", "eating an apple", "lying down and sleeping",
    ]);
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

  it("expression and facing groups match the spec", () => {
    expect(EXPRESSIONS.map((e) => [e.label, e.phrase])).toEqual([
      ["開心", "with a happy smiling expression"], ["大笑", "with a big laughing expression"],
      ["難過", "with a sad expression"], ["生氣", "with an angry expression"],
      ["驚訝", "with a surprised expression"], ["害羞", "with a shy blushing expression"],
      ["想睡", "with a sleepy expression"], ["害怕", "with a scared expression"],
    ]);
    expect(FACINGS.map((e) => [e.label, e.phrase])).toEqual([
      ["朝左", "facing toward the left side of the picture"], ["正面", "facing the viewer"],
      ["朝右", "facing toward the right side of the picture"],
    ]);
  });

  describe("setExclusivePhrase", () => {
    const happy = EXPRESSIONS[0].phrase, laugh = EXPRESSIONS[1].phrase;
    it("appends when the group has no phrase", () => {
      expect(setExclusivePhrase("waving one hand", EXPRESSIONS, laugh)).toBe("waving one hand, " + laugh);
      expect(setExclusivePhrase("", EXPRESSIONS, laugh)).toBe(laugh);
    });
    it("replaces another phrase of the same group, case-insensitively", () => {
      expect(setExclusivePhrase(`waving one hand, ${happy}, dancing`, EXPRESSIONS, laugh)).toBe(`waving one hand, dancing, ${laugh}`);
      expect(setExclusivePhrase("WITH A HAPPY SMILING EXPRESSION", EXPRESSIONS, laugh)).toBe(laugh);
      expect(setExclusivePhrase(`${happy} and waving`, EXPRESSIONS, laugh)).toBe(`and waving, ${laugh}`);
    });
    it("removes with null and keeps separators clean", () => {
      expect(setExclusivePhrase(`waving, ${happy}`, EXPRESSIONS, null)).toBe("waving");
      expect(setExclusivePhrase(`${happy}, waving`, EXPRESSIONS, null)).toBe("waving");
      expect(setExclusivePhrase(`a, ${happy}, b`, EXPRESSIONS, null)).toBe("a, b");
      expect(setExclusivePhrase(happy, EXPRESSIONS, null)).toBe("");
    });
    it("cleans a leftover full-width comma", () => {
      expect(setExclusivePhrase(`揮手, ${EXPRESSIONS[0].phrase}，跑步`, EXPRESSIONS, laugh)).toBe(`揮手, 跑步, ${laugh}`);
      expect(setExclusivePhrase(`揮手，${happy}`, EXPRESSIONS, null)).toBe("揮手");
    });
    it("leaves other groups alone", () => {
      const left = FACINGS[0].phrase;
      expect(setExclusivePhrase(`${happy}, ${left}`, EXPRESSIONS, laugh)).toBe(`${left}, ${laugh}`);
    });
  });
});
