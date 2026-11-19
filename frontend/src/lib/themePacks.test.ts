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
    expect(THEME_PACKS.map((p) => p.id)).toEqual(["vehicles", "jobs", "hygiene", "world", "taiwan"]);
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
  it("洗手：大洗手台畫在背景（使用者 2026-11-19 選），主角只畫搓手、不畫第二個水龍頭；位置在 C 頁自己拖", () => {
    expect(pages[0].scene).toContain("有檯面的大洗手台");
    expect(pages[0].action).not.toMatch(/洗手台|水龍頭/);
    expect(pages[0].action).toContain("搓");
  });
  it("洗澡：浴缸只在動作裡，背景沒有浴缸", () => {
    expect(pages[2].action).toContain("浴缸");
    expect(pages[2].scene).toContain("沒有浴缸");
  });
});

const WORLD: [string, string][] = [
  ["法國・艾菲爾鐵塔", "Eiffel Tower"],
  ["美國・自由女神像", "Statue of Liberty"],
  ["中國・萬里長城", "Great Wall of China"],
  ["埃及・金字塔", "Pyramids of Giza"],
  ["埃及・人面獅身像", "Great Sphinx"],
  ["義大利・比薩斜塔", "Leaning Tower of Pisa"],
  ["義大利・羅馬競技場", "Colosseum"],
  ["英國・大笨鐘", "Big Ben"],
  ["英國・倫敦塔橋", "Tower Bridge"],
  ["澳洲・雪梨歌劇院", "Sydney Opera House"],
  ["印度・泰姬瑪哈陵", "Taj Mahal"],
  ["日本・富士山", "Mount Fuji"],
  ["台灣・台北101", "Taipei 101"],
  ["柬埔寨・吳哥窟", "Angkor Wat"],
  ["祕魯・馬丘比丘", "Machu Picchu"],
  ["美國・金門大橋", "Golden Gate Bridge"],
  ["西班牙・聖家堂", "Sagrada Família"],
  ["德國・布蘭登堡門", "Brandenburg Gate"],
  ["智利・摩艾石像", "Moai Statues"],
  ["俄羅斯・聖瓦西里大教堂", "St. Basil's Cathedral"],
];

describe("theme pack world", () => {
  const pack = THEME_PACKS.find((p) => p.id === "world")!;
  const parsed = parseStory(pack?.text ?? "");
  it("is 環遊世界 with 20 pages in order and no problems", () => {
    expect(pack.title).toBe("環遊世界");
    expect(parsed.errors).toEqual([]);
    expect(parsed.pages.map((p) => p.n)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
  });
  it("captions are 國家・地標 / English name", () => {
    expect(parsed.pages.map((p) => [p.captionZh, p.captionEn])).toEqual(WORLD);
  });
  it("landmarks stay in the background with no people; the character only holds small things", () => {
    for (const [i, p] of parsed.pages.entries()) {
      expect(p.scene.endsWith("，畫面裡沒有人")).toBe(true);
      expect(p.scene).toContain(WORLD[i][0].split("・")[1].replace("台北101", "101"));
    }
  });
  it("is within limits", () => {
    for (const p of parsed.pages) {
      expect(countChars(p.scene)).toBeLessThanOrEqual(MAX_SCENE);
      expect(countChars(p.action)).toBeLessThanOrEqual(MAX_ACTION);
      expect(countChars(p.captionZh ?? "")).toBeLessThanOrEqual(MAX_CAPTION_ZH);
      expect(countChars(p.captionEn ?? "")).toBeLessThanOrEqual(MAX_CAPTION_EN);
    }
  });
  it("比薩斜塔：最常見的「扶住斜塔」拍照姿勢，要笑（使用者 2026-11-19）", () => {
    expect(parsed.pages[5].action).toContain("撐住");
    expect(parsed.pages[5].action).toContain("笑");
  });
  it("actions never name the landmark (it would be drawn again in the character's hands)", () => {
    for (const [i, p] of parsed.pages.entries()) {
      const name = WORLD[i][0].split("・")[1];
      expect(p.action).not.toContain(name.slice(0, 2));
      expect(p.action).not.toContain(name.slice(-2));
    }
  });
});

const TAIWAN: [string, string][] = [
  ["台北・故宮博物院", "National Palace Museum"],
  ["台北・陽明山", "Yangmingshan"],
  ["新北・九份老街", "Jiufen Old Street"],
  ["新北・平溪天燈", "Pingxi Sky Lanterns"],
  ["新北・十分瀑布", "Shifen Waterfall"],
  ["新北・淡水漁人碼頭", "Tamsui Fisherman's Wharf"],
  ["苗栗・勝興車站", "Shengxing Station"],
  ["台中・高美濕地", "Gaomei Wetlands"],
  ["彰化・八卦山大佛", "Baguashan Great Buddha"],
  ["南投・清境農場", "Cingjing Farm"],
  ["台南・赤崁樓", "Chihkan Tower"],
  ["台南・安平古堡", "Anping Fort"],
  ["台南・孔廟", "Tainan Confucius Temple"],
  ["高雄・蓮池潭龍虎塔", "Dragon and Tiger Pagodas"],
  ["屏東・鵝鑾鼻燈塔", "Eluanbi Lighthouse"],
  ["屏東・墾丁帆船石", "Sail Rock, Kenting"],
  ["花蓮・太魯閣", "Taroko Gorge"],
  ["花蓮・七星潭", "Qixingtan Beach"],
  ["台東・伯朗大道", "Mr. Brown Avenue"],
  ["台東・三仙台", "Sanxiantai Bridge"],
  ["澎湖・雙心石滬", "Twin Hearts Stone Weir"],
  ["金門・風獅爺", "Wind Lion God"],
];

describe("theme pack taiwan", () => {
  const pack = THEME_PACKS.find((p) => p.id === "taiwan")!;
  const parsed = parseStory(pack?.text ?? "");
  it("is 台灣景點 with 22 pages in order and no problems (user 2026-11-19: 22 is fine)", () => {
    expect(pack.title).toBe("台灣景點");
    expect(parsed.errors).toEqual([]);
    expect(parsed.pages.map((p) => p.n)).toEqual(Array.from({ length: 22 }, (_, i) => i + 1));
  });
  it("captions are 縣市・景點 / English name", () => {
    expect(parsed.pages.map((p) => [p.captionZh, p.captionEn])).toEqual(TAIWAN);
  });
  it("spots stay in the background with no people", () => {
    for (const [i, p] of parsed.pages.entries()) {
      expect(p.scene.endsWith("，畫面裡沒有人")).toBe(true);
      expect(p.scene).toContain(TAIWAN[i][0].split("・")[1].slice(0, 2));
    }
  });
  it("no metaphors in scenes (they get drawn literally: 像皇冠 → a crown, 像碗 → a bowl)", () => {
    for (const p of parsed.pages) expect(p.scene).not.toMatch(/像[^，]*(皇冠|碗|烏龜|帆船)/);
  });
  it("actions never name the spot (it would be drawn again in the character's hands)", () => {
    for (const [i, p] of parsed.pages.entries()) {
      const name = TAIWAN[i][0].split("・")[1];
      expect(p.action).not.toContain(name.slice(0, 2));
      expect(p.action).not.toContain(name.slice(-2));
    }
  });
  it("is within limits", () => {
    for (const p of parsed.pages) {
      expect(countChars(p.scene)).toBeLessThanOrEqual(MAX_SCENE);
      expect(countChars(p.action)).toBeLessThanOrEqual(MAX_ACTION);
      expect(countChars(p.captionZh ?? "")).toBeLessThanOrEqual(MAX_CAPTION_ZH);
      expect(countChars(p.captionEn ?? "")).toBeLessThanOrEqual(MAX_CAPTION_EN);
    }
  });
});

describe("fewer small regions (user 2026-11-19)", () => {
  it("every pack makes pages with simple line detail", () => {
    for (const pack of THEME_PACKS) expect(pack.lineDetail).toBe("simple");
  });
  it("landmark packs ask for simple backgrounds with big shapes", () => {
    for (const id of ["world", "taiwan"]) {
      for (const p of parseStory(THEME_PACKS.find((x) => x.id === id)!.text).pages)
        expect(p.scene).toContain("背景簡單、大塊的形狀、細節少");
    }
  });
});
