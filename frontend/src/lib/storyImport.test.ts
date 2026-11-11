import { describe, expect, it } from "vitest";
import { countChars, MAX_CAPTION_EN, MAX_CAPTION_ZH } from "./limits";
import { parseStory, planImport } from "./storyImport";

const two = "第1頁\n場景：森林\n動作：跑步\n第2頁\n場景：海邊\n動作：游泳";

describe("parseStory", () => {
  it("parses labelled blocks with 第N頁 headings", () => {
    expect(parseStory(two)).toEqual({
      pages: [
        { n: 1, scene: "森林", action: "跑步" },
        { n: 2, scene: "海邊", action: "游泳" },
      ],
      errors: [],
    });
  });

  it.each(["1.", "1、", "1．", "P1", "p1", "Page 1", "第 1 頁："])("accepts %s heading", (h) => {
    const r = parseStory(`${h}\n場景：森林\n動作：跑步`);
    expect(r.pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
  });

  it("accepts a heading followed by label text on the same line", () => {
    expect(parseStory("第1頁 場景：森林\n動作：跑步").pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
  });

  it("accepts 背景/角色 labels and half-width colons", () => {
    expect(parseStory("P1\n背景:森林\n角色:跑步").pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
  });

  it("handles CRLF, full-width digits, extra blank lines and **bold** labels", () => {
    const t = "\r\n\r\n**第１頁**\r\n\r\n\r\n**場景：**森林\r\n\r\n**動作**：跑步\r\n\r\n> 第２頁\r\n## 場景：海邊\r\n動作：游泳**\r\n";
    expect(parseStory(t)).toEqual({
      pages: [
        { n: 1, scene: "森林", action: "跑步" },
        { n: 2, scene: "海邊", action: "游泳" },
      ],
      errors: [],
    });
  });

  it("multi-line label text continues until next label", () => {
    const r = parseStory("第1頁\n場景：森林\n有一棵大樹\n動作：跑步\n很開心\n第2頁\n場景：海");
    expect(r.pages[0]).toEqual({ n: 1, scene: "森林\n有一棵大樹", action: "跑步\n很開心" });
    expect(r.pages[1]).toEqual({ n: 2, scene: "海", action: "" });
  });

  it("blocks without headings become consecutive pages", () => {
    const r = parseStory("場景：森林\n動作：跑步\n\n\n場景：海邊\n動作：游泳\n\n場景：山");
    expect(r.pages.map((p) => p.n)).toEqual([1, 2, 3]);
    expect(r.pages[1].scene).toBe("海邊");
  });

  it("TSV: a first row that merely mentions 背景/場景 is data, not a header", () => {
    const r = parseStory("森林的背景有大樹\t跑步\n海邊\t游泳");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林的背景有大樹", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
  });

  it("TSV: header columns decide which column is which (頁 | 場景 | 動作, 動作 | 背景)", () => {
    expect(parseStory("頁\t場景\t動作\n1\t森林\t跑步\n2\t海邊\t游泳").pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
    expect(parseStory("動作\t背景\n跑步\t森林").pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
    expect(parseStory("頁數\t角色\t場景\n1\t跑步\t森林").pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
  });

  it("TSV with header row", () => {
    const r = parseStory("場景\t動作\n森林\t跑步\n海邊\t游泳\n");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
    expect(parseStory("森林\t跑步").pages).toHaveLength(1);
  });

  it("block without labels between labelled blocks is an error with its page number", () => {
    const r = parseStory("場景：森林\n動作：跑步\n\n這是一段沒有標籤的文字\n\n場景：海邊");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 3, scene: "海邊", action: "" },
    ]);
    expect(r.errors).toEqual([{ n: 2, excerpt: "這是一段沒有標籤的文字", reason: "找不到「場景：」或「動作：」" }]);
  });

  it("no headings: an unlabelled preamble does not take page 1, a sign-off is not a page (n: null)", () => {
    const r = parseStory("好的，以下是分鏡：\n\n場景：森林\n動作：跑步\n\n場景：海邊\n動作：游泳\n\n希望對你有幫助！");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
    expect(r.errors).toEqual([
      { n: null, excerpt: "好的，以下是分鏡：", reason: "找不到「場景：」或「動作：」" },
      { n: null, excerpt: "希望對你有幫助！", reason: "找不到「場景：」或「動作：」" },
    ]);
  });

  it("no headings: a repeated label inside one block starts the next page", () => {
    const r = parseStory("場景：森林\n動作：跑步\n場景：海邊\n動作：游泳");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
  });

  it("text on the heading line is cleaned like any other line (bold label)", () => {
    expect(parseStory("**第1頁** **場景：** 森林\n動作：跑步").pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
  });

  it("strips markdown bullets - • ・ before labels", () => {
    const r = parseStory("第1頁\n- 場景：森林\n• 動作：跑步\n第2頁\n・場景：海邊\n- 動作：游泳");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
  });

  it("number-led continuation lines (3.5公尺、3. 隻小鳥) are not headings", () => {
    const r = parseStory("第1頁\n場景：一棵\n3.5公尺高的樹\n動作：樹上有\n3. 隻小鳥\n第2頁\n場景：海邊");
    expect(r.pages).toEqual([
      { n: 1, scene: "一棵\n3.5公尺高的樹", action: "樹上有\n3. 隻小鳥" },
      { n: 2, scene: "海邊", action: "" },
    ]);
    // 沒有其他標題時也一樣：「3. 隻小鳥」不會把後面切成新的一頁
    expect(parseStory("場景：樹上有\n3. 隻小鳥\n動作：飛").pages).toEqual([{ n: 1, scene: "樹上有\n3. 隻小鳥", action: "飛" }]);
  });

  it("numbered headings in order still work, with a title after the number", () => {
    const r = parseStory("1. 森林的早晨\n場景：森林\n動作：跑步\n2. 海邊的午後\n場景：海邊\n動作：游泳");
    expect(r.pages).toEqual([
      { n: 1, scene: "森林", action: "跑步" },
      { n: 2, scene: "海邊", action: "游泳" },
    ]);
  });

  it.each(["第一頁", "第十頁", "第十二頁", "第二十頁", "第三十五頁", "第四十頁", "一、"])(
    "accepts Chinese numeral heading %s with text on the same line",
    (h) => {
      expect(parseStory(`${h} 場景：森林\n動作：跑步`)).toEqual({ pages: [{ n: 1, scene: "森林", action: "跑步" }], errors: [] });
    },
  );

  it("Chinese numeral headings split pages (第一頁／第二頁, 一、／二、)", () => {
    const want = [
      { n: 1, scene: "森林", action: "" },
      { n: 2, scene: "海邊", action: "" },
    ];
    expect(parseStory("第一頁\n場景：森林\n第二頁\n場景：海邊").pages).toEqual(want);
    expect(parseStory("一、\n場景：森林\n二、\n場景：海邊").pages).toEqual(want);
  });

  it("more than 40 pages flags the rest", () => {
    const t = Array.from({ length: 42 }, (_, i) => `第${i + 1}頁\n場景：地點${i + 1}`).join("\n");
    const r = parseStory(t);
    expect(r.pages).toHaveLength(40);
    expect(r.errors.map((e) => [e.n, e.reason])).toEqual([
      [41, "超過 40 頁，這次不匯入"],
      [42, "超過 40 頁，這次不匯入"],
    ]);
  });

  it("ignores a preamble before the first heading", () => {
    const r = parseStory("好的，以下是分鏡：\n\n第1頁\n場景：森林\n動作：跑步");
    expect(r).toEqual({ pages: [{ n: 1, scene: "森林", action: "跑步" }], errors: [] });
  });

  it("does not append a trailing sign-off to the last action", () => {
    const r = parseStory("第1頁\n場景：森林\n動作：跑步\n\n希望對你有幫助！");
    expect(r.pages).toEqual([{ n: 1, scene: "森林", action: "跑步" }]);
    expect(r.errors).toEqual([]);
  });

  it("empty input gives nothing", () => {
    expect(parseStory("  \n\n")).toEqual({ pages: [], errors: [] });
  });
});

describe("planImport", () => {
  const ex = [
    { position: 2, scene_prompt: "", action_prompt: "舊動作" },
    { position: 1, scene_prompt: "舊場景", action_prompt: "" },
  ];
  it("fills only empty fields", () => {
    const rows = planImport(
      [
        { n: 1, scene: "新", action: "新A" },
        { n: 2, scene: "新B", action: "新C" },
      ],
      ex,
    );
    expect(rows[0]).toMatchObject({ create: false, fill: { scene: false, action: true }, problems: [] });
    expect(rows[1]).toMatchObject({ create: false, fill: { scene: true, action: false } });
  });

  it("creates pages past the existing count", () => {
    const rows = planImport([{ n: 3, scene: "s", action: "" }], ex);
    expect(rows[0]).toMatchObject({ create: true, fill: { scene: true, action: false } });
  });

  it("flags over-long scene (801) and action (301) and does not apply them", () => {
    const rows = planImport(
      [
        { n: 1, scene: "x".repeat(801), action: "a" },
        { n: 2, scene: "x".repeat(800), action: "y".repeat(301) },
      ],
      [],
    );
    expect(rows[0].problems).toEqual(["場景超過 800 字"]);
    expect(rows[1].problems).toEqual(["動作超過 300 字"]);
  });
});

describe("captions", () => {
  it("counts code points, not UTF-16 units", () => {
    expect(countChars("𠮷")).toBe(1);
    expect(MAX_CAPTION_ZH).toBe(12);
    expect(MAX_CAPTION_EN).toBe(28);
  });

  it.each([
    ["中文：消防車", "英文：Fire Engine"],
    ["中文:消防車", "English: Fire Engine"],
    ["中文：消防車", "english：Fire Engine"],
    ["中文：消防車", "ENGLISH:Fire Engine"],
  ])("reads %s / %s anywhere after the heading, in any order", (zh, en) => {
    for (const body of [`場景：消防局\n動作：開車\n${zh}\n${en}`, `${en}\n${zh}\n場景：消防局\n動作：開車`, `場景：消防局\n${en}\n動作：開車\n${zh}`]) {
      expect(parseStory(`第1頁\n${body}`).pages).toEqual([
        { n: 1, scene: "消防局", action: "開車", captionZh: "消防車", captionEn: "Fire Engine" },
      ]);
    }
  });

  it("works without headings (a repeated label starts the next page)", () => {
    const r = parseStory("場景：a\n動作：b\n中文：甲\n英文：A\n場景：c\n動作：d\n中文：乙");
    expect(r.pages).toEqual([
      { n: 1, scene: "a", action: "b", captionZh: "甲", captionEn: "A" },
      { n: 2, scene: "c", action: "d", captionZh: "乙" },
    ]);
  });

  it("does not treat 「文字：」 as a caption", () => {
    const r = parseStory("第1頁\n場景：a\n動作：b\n文字：消防車");
    expect(r.pages).toEqual([{ n: 1, scene: "a", action: "b\n文字：消防車" }]);
    expect(r.pages[0]).not.toHaveProperty("captionZh");
  });

  it("a heading line never yields a caption", () => {
    for (const h of ["第1頁 中文：消防車", "第1頁 English: Fire", "1. 英文：Fire"]) {
      const r = parseStory(`${h}\n場景：a\n動作：b`);
      expect(r.pages).toHaveLength(1);
      expect(r.pages[0]).not.toHaveProperty("captionZh");
      expect(r.pages[0]).not.toHaveProperty("captionEn");
    }
  });

  it("spreadsheet columns 中文 / 英文 / English", () => {
    const r = parseStory("場景\t動作\t中文\tEnglish\n消防局\t開車\t消防車\tFire Engine\n醫院\t揮手\t\t");
    expect(r.pages).toEqual([
      { n: 1, scene: "消防局", action: "開車", captionZh: "消防車", captionEn: "Fire Engine" },
      { n: 2, scene: "醫院", action: "揮手" },
    ]);
    const r2 = parseStory("英文\t中文\t場景\t動作\nFire\t火\ta\tb");
    expect(r2.pages).toEqual([{ n: 1, scene: "a", action: "b", captionZh: "火", captionEn: "Fire" }]);
  });

  it("caption-only spreadsheet header: no positional fallback, scene and action stay empty", () => {
    const r = parseStory("中文\t英文\n消防車\tFire Engine\n救護車\tAmbulance");
    expect(r.errors).toEqual([]);
    expect(r.pages).toEqual([
      { n: 1, scene: "", action: "", captionZh: "消防車", captionEn: "Fire Engine" },
      { n: 2, scene: "", action: "", captionZh: "救護車", captionEn: "Ambulance" },
    ]);
    const rows = planImport(r.pages, []);
    expect(rows[0]).toMatchObject({ scene: "", action: "", fill: { scene: false, action: false, captionZh: true, captionEn: true } });
  });

  const ex = [{ position: 1, scene_prompt: "", action_prompt: "", caption_zh: "舊", caption_en: null }];
  it("fills captions only where empty", () => {
    const rows = planImport([{ n: 1, scene: "s", action: "a", captionZh: "新", captionEn: "New" }], ex);
    expect(rows[0].captionZh).toBe("新");
    expect(rows[0].fill).toEqual({ scene: true, action: true, captionZh: false, captionEn: true });
    const rows2 = planImport([{ n: 2, scene: "s", action: "a", captionZh: "新" }], ex);
    expect(rows2[0]).toMatchObject({ create: true, fill: { captionZh: true, captionEn: false } });
  });

  it("reports over-limit captions with the spec messages, counting code points", () => {
    const ok = planImport([{ n: 1, scene: "s", action: "a", captionZh: "𠮷".repeat(12), captionEn: "x".repeat(28) }], []);
    expect(ok[0].problems).toEqual([]);
    const bad = planImport([{ n: 1, scene: "s", action: "a", captionZh: "𠮷".repeat(13), captionEn: "x".repeat(29) }], []);
    expect(bad[0].problems).toEqual(["中文字最多 12 個字", "英文字最多 28 個字元"]);
  });
});

describe("貼上的頁面文字也正規化", () => {
  it("零寬字元與特殊空白在解析時處理，字數用正規化後的", () => {
    const r = parseStory("第1頁\n場景：消防局\n動作：開車\n中文：消​防 車 \n英文：Fire\u0085Engine");
    expect(r.pages[0].captionZh).toBe("消防 車");
    expect(r.pages[0].captionEn).toBe("Fire Engine");
  });

  it("只有零寬字元的文字等於沒寫", () => {
    const r = parseStory("第1頁\n場景：消防局\n動作：開車\n中文：​");
    expect(r.pages[0].captionZh).toBeUndefined();
  });

  it("試算表欄位同樣正規化", () => {
    const r = parseStory("場景\t動作\t中文\t英文\n消防局\t開車\t消​防\t​");
    expect(r.pages[0].captionZh).toBe("消防");
    expect(r.pages[0].captionEn).toBeUndefined();
  });
});
