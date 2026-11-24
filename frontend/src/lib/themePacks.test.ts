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
    expect(THEME_PACKS.map((p) => p.id)).toEqual(["vehicles", "jobs", "hygiene", "world", "taiwan", "holidays", "zoo", "sports", "frames"]);
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
  ["台北・中正紀念堂", "CKS Memorial Hall"],
  ["台北・陽明山", "Yangmingshan"],
  ["新北・九份老街", "Jiufen Old Street"],
  ["新北・平溪天燈", "Pingxi Sky Lanterns"],
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
  it("中正紀念堂取代十分瀑布（使用者 2026-11-19 選的）；漁人碼頭寫出情人橋的斜柱與鋼索", () => {
    const at = (n: number) => parsed.pages[n - 1];
    expect(at(2).scene).toContain("藍色的八角形尖屋頂");
    expect(at(6).scene).toContain("鋼索");
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
  it("every pack makes pages with simple line detail (except 華麗相框: medium, user 2026-11-24)", () => {
    for (const pack of THEME_PACKS.filter((x) => x.id !== "frames")) expect(pack.lineDetail).toBe("simple");
  });
  it("landmark packs ask for simple backgrounds with big shapes", () => {
    for (const id of ["world", "taiwan", "holidays", "zoo", "sports"]) {
      for (const p of parseStory(THEME_PACKS.find((x) => x.id === id)!.text).pages)
        expect(p.scene).toContain("背景簡單、大塊的形狀、細節少");
    }
  });
});

/** 節日、動物園、運動（使用者 2026-11-19 確認清單，各 12 頁）。 */
const MORE_PACKS: [string, string, [string, string][]][] = [
  [
    "holidays",
    "節日",
    [
      ["新年快樂", "Happy New Year"],
      ["過年", "Lunar New Year"],
      ["元宵節", "Lantern Festival"],
      ["兒童節", "Children's Day"],
      ["母親節", "Mother's Day"],
      ["端午節", "Dragon Boat Festival"],
      ["父親節", "Father's Day"],
      ["中秋節", "Mid-Autumn Festival"],
      ["教師節", "Teachers' Day"],
      ["萬聖節", "Halloween"],
      ["聖誕節", "Christmas"],
      ["生日快樂", "Happy Birthday"],
    ],
  ],
  [
    "zoo",
    "動物園",
    [
      ["長頸鹿", "Giraffe"],
      ["大象", "Elephant"],
      ["獅子", "Lion"],
      ["貓熊", "Giant Panda"],
      ["猴子", "Monkeys"],
      ["企鵝", "Penguins"],
      ["斑馬", "Zebras"],
      ["河馬", "Hippo"],
      ["無尾熊", "Koala"],
      ["袋鼠", "Kangaroo"],
      ["紅鶴", "Flamingos"],
      ["鱷魚", "Crocodile"],
    ],
  ],
  [
    "sports",
    "運動",
    [
      ["溜冰", "Ice Skating"],
      ["足球", "Soccer"],
      ["棒球", "Baseball"],
      ["高爾夫球", "Golf"],
      ["排球", "Volleyball"],
      ["美式足球", "American Football"],
      ["撞球", "Billiards"],
      ["跳繩", "Jump Rope"],
      ["跑步", "Running"],
      ["滑板", "Skateboarding"],
      ["體操", "Gymnastics"],
      ["登山", "Hiking"],
    ],
  ],
];

describe.each(MORE_PACKS)("theme pack %s", (id, title, captions) => {
  const pack = THEME_PACKS.find((p) => p.id === id)!;
  const parsed = parseStory(pack?.text ?? "");
  it("exists with title, description and 12 pages in order with no problems", () => {
    expect(pack.title).toBe(title);
    expect(pack.description).not.toBe("");
    expect(parsed.errors).toEqual([]);
    expect(parsed.pages.map((p) => p.n)).toEqual(Array.from({ length: 12 }, (_, i) => i + 1));
  });
  it("captions match the list", () => {
    expect(parsed.pages.map((p) => [p.captionZh, p.captionEn])).toEqual(captions);
  });
  it("simple big-shape backgrounds with no people", () => {
    for (const p of parsed.pages) expect(p.scene.endsWith("，背景簡單、大塊的形狀、細節少，畫面裡沒有人")).toBe(true);
  });
  it("no metaphors (像… gets drawn literally)", () => {
    for (const p of parsed.pages) {
      expect(p.scene).not.toContain("像");
      expect(p.action).not.toContain("像");
    }
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

describe("動物園：動物在背景，動作不提動物（否則手上會再畫一隻）", () => {
  const parsed = parseStory(THEME_PACKS.find((p) => p.id === "zoo")?.text ?? "");
  const animals = MORE_PACKS[1][2].map(([zh]) => zh);
  it("each scene names its animal; no action names any animal", () => {
    for (const [i, p] of parsed.pages.entries()) {
      expect(p.scene).toContain(animals[i]);
      for (const a of animals) expect(p.action).not.toContain(a);
    }
  });
});

describe("驗收後修正（使用者 2026-11-19）", () => {
  const pages = (id: string) => parseStory(THEME_PACKS.find((p) => p.id === id)?.text ?? "").pages;
  it("節日：新年只畫三朵大煙火；過年紅紙不寫字（會畫成亂字）；端午主角坐在龍舟上（船在背景畫不出來）", () => {
    const h = pages("holidays");
    expect(h[0].scene).toContain("三朵大大的煙火");
    expect(h[0].scene).toContain("天空其他地方是空的");
    expect(h[0].scene).toContain("沒有星星和雲"); // 夜空的星星、雲做成線稿會變成一大片細碎點點
    expect(h[1].scene).toContain("沒有字");
    expect(h[5].action).toContain("坐在龍舟上");
    expect(h[5].scene).not.toContain("龍舟");
  });
  it("動物園：動物在畫面左邊或右邊、左右輪流（置中的主角會擋住）", () => {
    for (const [i, p] of pages("zoo").entries()) expect(p.scene).toContain(i % 2 === 0 ? "畫面右邊" : "畫面左邊");
    expect(pages("zoo")[9].scene).toContain("袋鼠站在草地上"); // 「在草地上跳」那一版袋鼠沒畫出來
  });
  it("運動：體操用鞍馬（使用者 2026-11-20）；鞍馬跟主角畫在一起，背景只有軟墊", () => {
    const gym = pages("sports")[10];
    expect(gym.scene).toContain("軟墊");
    expect(gym.scene).not.toContain("球場");
    expect(gym.scene).not.toContain("鞍馬");
    expect(gym.action).toContain("鞍馬");
  });
  it("動物園：主角小一點、不放中間，放在動物的另一邊（使用者 2026-11-20）", () => {
    const zoo = THEME_PACKS.find((p) => p.id === "zoo")!;
    expect(zoo.placements).toHaveLength(12);
    for (const [i, pl] of (zoo.placements ?? []).entries()) {
      expect(pl.height).toBe(0.42);
      expect(pl.cx).toBe(i % 2 === 0 ? 0.26 : 0.74); // 動物在右邊 → 主角在左邊
    }
  });
  it("其他主題包不帶位置", () => {
    for (const p of THEME_PACKS.filter((x) => x.id !== "zoo" && x.id !== "frames")) expect(p.placements).toBeUndefined();
  });
});

describe("華麗相框（使用者 2026-11-24）", () => {
  const pack = THEME_PACKS.find((p) => p.id === "frames");
  const parsed = parseStory(pack?.text ?? "");
  const SHAPES = ["橢圓", "長方形", "圓形", "四葉草", "六角形", "花環", "拱門", "盾牌", "方形", "蝴蝶結", "皇冠", "菱形"];
  it("is the 華麗相框 pack with 12 pages and shape captions", () => {
    expect(pack?.title).toBe("華麗相框");
    expect(parsed.errors).toEqual([]);
    expect(parsed.pages.map((p) => [p.captionZh, p.captionEn])).toEqual([
      ["橢圓形", "Oval"],
      ["長方形", "Rectangle"],
      ["圓形", "Circle"],
      ["四葉草", "Four-Leaf Clover"],
      ["六角形", "Hexagon"],
      ["花環", "Wreath"],
      ["拱門", "Arch"],
      ["盾牌", "Shield"],
      ["圓角方形", "Rounded Square"],
      ["蝴蝶結", "Bow"],
      ["皇冠", "Crown"],
      ["菱形", "Diamond"],
    ]);
  });
  it("every scene is an empty ornate frame of its shape, lines only, no people", () => {
    for (const [i, p] of parsed.pages.entries()) {
      expect(p.scene).toContain(SHAPES[i]);
      expect(p.scene).toContain("古典捲草花紋外框");
      expect(p.scene).toContain("外框幾乎佔滿整張紙"); // 框小的話 Qwen 會在旁邊畫一堆東西
      expect(p.scene).toContain("框裡面是乾淨的白色，什麼都沒有");
      expect(p.scene).not.toContain("白邊"); // 「四周只留一點白邊」會多畫一圈方框（使用者 2026-11-24 不要）
      for (const named of ["草叢", "沒有地面", "方形的邊框"]) expect(p.scene).not.toContain(named); // 點名排除反而會畫出來
      expect(p.scene).toContain("除了這個外框，畫面裡沒有任何其他東西");
      expect(p.scene).not.toContain("空白"); // 會被畫成透明格子
      expect(p.scene.endsWith("，只有黑色線條、沒有塗黑的地方，畫面裡沒有人")).toBe(true);
      expect(p.scene).not.toContain("像"); // 比喻會被照字面畫出來
      expect(p.action).not.toContain("像");
    }
  });
  it("驗收後：圓形框不被切掉；v2 的四葉草、六角形、圓角方形措辭；花環是直立橢圓形", () => {
    expect(parsed.pages[2].scene).toContain("整個圓形外框都在紙張裡面，沒有被切掉");
    expect(parsed.pages[3].scene).toContain("空心四葉草形");
    expect(parsed.pages[3].scene).toContain("四葉草裡面是乾淨的白色");
    expect(parsed.pages[4].scene).toContain("最上面和最下面是尖角，左右兩邊是直的，總共六條邊");
    expect(parsed.pages[5].scene).toContain("直立橢圓形花環");
    expect(parsed.pages[8].scene).toContain("直立方形");
    expect(parsed.pages[8].scene).toContain("四個角是圓角");
  });
  it("v2 actions for the crown and diamond pages", () => {
    expect(parsed.pages[10].action).toBe("頭上戴著一頂小皇冠，雙手合在胸前微笑");
    expect(parsed.pages[11].action).toBe("一手比 V，開心地笑");
  });
  it("description names the portrait", () => {
    expect(pack?.description).toBe("12 種形狀的古典花紋相框，主角是框裡的半身肖像，每頁有中英文字");
  });
  it("objects in the frame aren't repeated in the action (drawn twice otherwise)", () => {
    for (const p of parsed.pages) {
      expect(p.action).not.toContain("框");
      expect(p.action).not.toContain("蝴蝶結");
    }
  });
  it("is within limits", () => {
    for (const p of parsed.pages) {
      expect(countChars(p.scene)).toBeLessThanOrEqual(MAX_SCENE);
      expect(countChars(p.action)).toBeLessThanOrEqual(MAX_ACTION);
    }
  });
  it("medium detail; character small and raised into the frame", () => {
    expect(pack?.lineDetail).toBe("standard");
    expect(pack?.placements).toHaveLength(12);
    for (const pl of pack?.placements ?? []) expect(pl).toEqual({ cx: 0.5, cy: 0.59, height: 0.42, flip: false, frame: { x: 0.5, y: 0.55 }, autofit: true });
  });
});
