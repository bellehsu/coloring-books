import { describe, expect, it } from "vitest";
import { countChars, MAX_ACTION, MAX_CAPTION_EN, MAX_CAPTION_ZH, MAX_SCENE } from "./limits";
import { parseStory } from "./storyImport";
import { THEME_PACKS } from "./themePacks";

const CAPTIONS = [
  ["消防車", "Fire Engine"],
  ["救護車", "Ambulance"],
  ["警車", "Police Car"],
  ["公車", "Bus"],
  ["計程車", "Taxi"],
  ["火車", "Train"],
  ["飛機", "Airplane"],
  ["帆船", "Sailboat"],
  ["腳踏車", "Bicycle"],
  ["直升機", "Helicopter"],
];

describe("THEME_PACKS", () => {
  const pack = THEME_PACKS.find((p) => p.id === "vehicles")!;
  const parsed = parseStory(pack.text);

  it("has the vehicles pack", () => {
    expect(THEME_PACKS.map((p) => p.id)).toEqual(["vehicles", "jobs", "hygiene"]);
    expect(pack.title).toBe("交通工具");
    expect(pack.description).not.toBe("");
  });
  it("parses to 10 pages in order with no problems", () => {
    expect(parsed.errors).toEqual([]);
    expect(parsed.pages.map((p) => p.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });
  it("every scene ends with the no-vehicle suffix", () => {
    for (const p of parsed.pages) expect(p.scene.endsWith("，沒有任何車輛、船、飛機或火車")).toBe(true);
  });
  it("captions match the list", () => {
    expect(parsed.pages.map((p) => [p.captionZh, p.captionEn])).toEqual(CAPTIONS);
  });
  it("is within limits", () => {
    for (const p of parsed.pages) {
      expect(countChars(p.scene)).toBeLessThanOrEqual(MAX_SCENE);
      expect(p.action.length).toBeGreaterThan(0);
      expect(countChars(p.action)).toBeLessThanOrEqual(MAX_ACTION);
      expect(countChars(p.captionZh ?? "")).toBeLessThanOrEqual(MAX_CAPTION_ZH);
      expect(countChars(p.captionEn ?? "")).toBeLessThanOrEqual(MAX_CAPTION_EN);
    }
  });
  it("uses the spec wording", () => {
    expect(parsed.pages[0].scene).toBe("消防局門口，車庫門關著，旁邊有消防栓，沒有任何車輛、船、飛機或火車");
    expect(parsed.pages[9].action).toBe("坐在直升機裡，從窗戶揮手，畫出整台直升機的外觀");
  });
});

const NEW_PACKS: [string, string, [string, string][]][] = [
  [
    "jobs",
    "職業",
    [
      ["消防員", "Firefighter"],
      ["警察", "Police Officer"],
      ["醫生", "Doctor"],
      ["護理師", "Nurse"],
      ["廚師", "Chef"],
      ["老師", "Teacher"],
      ["農夫", "Farmer"],
      ["郵差", "Mail Carrier"],
      ["麵包師傅", "Baker"],
      ["太空人", "Astronaut"],
    ],
  ],
  [
    "hygiene",
    "衛生習慣",
    [
      ["洗手", "Wash Your Hands"],
      ["刷牙", "Brush Your Teeth"],
      ["洗澡", "Take a Bath"],
      ["洗臉", "Wash Your Face"],
      ["梳頭髮", "Comb Your Hair"],
      ["擦鼻涕", "Wipe Your Nose"],
      ["摀住口鼻", "Cover Your Sneeze"],
      ["戴口罩", "Wear a Mask"],
      ["換乾淨衣服", "Put On Clean Clothes"],
      ["喝白開水", "Drink Water"],
    ],
  ],
];

describe.each(NEW_PACKS)("theme pack %s", (id, title, captions) => {
  const pack = THEME_PACKS.find((p) => p.id === id)!;
  const parsed = parseStory(pack?.text ?? "");

  it("exists with a title and description", () => {
    expect(pack.title).toBe(title);
    expect(pack.description).not.toBe("");
  });
  it("parses to 10 pages in order with no problems", () => {
    expect(parsed.errors).toEqual([]);
    expect(parsed.pages.map((p) => p.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });
  it("keeps people out of the background", () => {
    for (const p of parsed.pages) expect(p.scene.endsWith("，畫面裡沒有人")).toBe(true);
  });
  it("captions match the list", () => {
    expect(parsed.pages.map((p) => [p.captionZh, p.captionEn])).toEqual(captions);
  });
  it("is within limits", () => {
    for (const p of parsed.pages) {
      expect(countChars(p.scene)).toBeLessThanOrEqual(MAX_SCENE);
      expect(p.action.length).toBeGreaterThan(0);
      expect(countChars(p.action)).toBeLessThanOrEqual(MAX_ACTION);
      expect(countChars(p.captionZh ?? "")).toBeLessThanOrEqual(MAX_CAPTION_ZH);
      expect(countChars(p.captionEn ?? "")).toBeLessThanOrEqual(MAX_CAPTION_EN);
    }
  });
});

describe("衛生習慣：主角用的東西跟主角畫在一起，背景不重複", () => {
  const pages = parseStory(THEME_PACKS.find((p) => p.id === "hygiene")!.text).pages;
  it("洗手：洗手台和水龍頭在動作裡，背景沒有洗手台", () => {
    expect(pages[0].action).toContain("洗手台的水龍頭");
    expect(pages[0].scene).toContain("沒有洗手台");
  });
  it("洗澡：浴缸只在動作裡，背景沒有浴缸", () => {
    expect(pages[2].action).toContain("浴缸");
    expect(pages[2].scene).toContain("沒有浴缸");
  });
});
