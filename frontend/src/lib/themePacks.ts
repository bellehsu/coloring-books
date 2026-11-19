/** 內建主題包：用和「貼上整本故事」相同的格式寫成文字，套用時走同一個解析器。 */

export interface ThemePack {
  id: string;
  title: string;
  description: string;
  text: string;
}

interface PackPage {
  scene: string;
  action: string;
  zh: string;
  en: string;
}

/** 照「貼上整本故事」的格式寫出每一頁（場景結尾加上 suffix）。 */
const packText = (pages: readonly PackPage[], suffix: string) =>
  pages.map((v, i) => `第${i + 1}頁\n場景：${v.scene}${suffix}\n動作：${v.action}\n中文：${v.zh}\n英文：${v.en}`).join("\n\n");

/** 每頁場景的結尾：避免背景圖裡出現交通工具。 */
const NO_VEHICLES = "，沒有任何車輛、船、飛機或火車";

const VEHICLES: readonly PackPage[] = [
  { scene: "消防局門口，車庫門關著，旁邊有消防栓", action: "開著紅色消防車，車上有長梯子，從車窗探出頭揮手", zh: "消防車", en: "Fire Engine" },
  { scene: "醫院門口，有大門和紅十字招牌", action: "站在白色救護車旁邊揮手，車身有紅十字", zh: "救護車", en: "Ambulance" },
  { scene: "警察局門口，有旗桿和台階", action: "坐在白色和藍色的警車駕駛座上，車頂有紅藍警燈", zh: "警車", en: "Police Car" },
  { scene: "公車站，有站牌和候車亭", action: "坐在黃色公車的駕駛座上揮手，畫出整台公車的外觀", zh: "公車", en: "Bus" },
  { scene: "城市馬路，有紅綠燈和斑馬線", action: "坐在黃色計程車的駕駛座上，車頂有計程車燈", zh: "計程車", en: "Taxi" },
  { scene: "火車站月台，有時鐘和長椅", action: "坐在火車頭上開心地笑", zh: "火車", en: "Train" },
  { scene: "機場，有塔台和跑道", action: "坐在小飛機的駕駛座上", zh: "飛機", en: "Airplane" },
  { scene: "海邊碼頭，有燈塔和海鷗", action: "坐在小帆船上划船", zh: "帆船", en: "Sailboat" },
  { scene: "公園的腳踏車道，有大樹和長椅", action: "騎著腳踏車", zh: "腳踏車", en: "Bicycle" },
  { scene: "山上的草地，有大石頭和小花", action: "坐在直升機裡，從窗戶揮手，畫出整台直升機的外觀", zh: "直升機", en: "Helicopter" },
];

/** 每頁場景的結尾：背景裡不要有人（主角由角色動作畫上去）。 */
const NO_PEOPLE = "，畫面裡沒有人";

/** 職業：主角穿自己原本的衣服、拿那個職業的工具（使用者 2026-11-18 選，主角一致性優先）。 */
const JOBS: readonly PackPage[] = [
  { scene: "消防局門口，車庫裡停著消防車，旁邊有消防栓", action: "雙手拿著消防水管往前噴水", zh: "消防員", en: "Firefighter" },
  { scene: "警察局門口，有旗桿和台階", action: "一手拿著哨子吹，一手舉起來比停止的手勢", zh: "警察", en: "Police Officer" },
  { scene: "診所看診室，有診療床和藥櫃", action: "脖子上掛著聽診器，手拿聽診器的聽頭往前伸", zh: "醫生", en: "Doctor" },
  { scene: "醫院病房，有病床和點滴架", action: "手拿體溫計和一個急救箱", zh: "護理師", en: "Nurse" },
  { scene: "餐廳廚房，有爐子和掛著的鍋子", action: "一手拿平底鍋、一手拿鍋鏟在炒菜", zh: "廚師", en: "Chef" },
  { scene: "教室，前面有寫著圖形的黑板和講桌", action: "一手拿著打開的書，一手拿教鞭指著前面", zh: "老師", en: "Teacher" },
  { scene: "農場菜園，有一排排蔬菜和稻草人", action: "雙手拿著澆水壺在澆水", zh: "農夫", en: "Farmer" },
  { scene: "住家門口，有信箱和小花圃", action: "背著郵差包，手拿一封信要放進信箱", zh: "郵差", en: "Mail Carrier" },
  { scene: "麵包店，櫃子裡擺滿麵包", action: "雙手端著一盤剛烤好的麵包", zh: "麵包師傅", en: "Baker" },
  { scene: "月球表面，有坑洞，遠方看得到地球", action: "手拿一面小旗子插在地上", zh: "太空人", en: "Astronaut" },
];

/** 衛生習慣：主角自己做這件事。 */
const HYGIENE: readonly PackPage[] = [
  { scene: "浴室裡有檯面的大洗手台，上面有水龍頭和肥皂，牆上有鏡子", action: "雙手在胸前搓洗，手上都是泡泡", zh: "洗手", en: "Wash Your Hands" },
  { scene: "浴室的洗手台前，牆上有鏡子", action: "一手拿牙刷刷牙，一手拿漱口杯", zh: "刷牙", en: "Brush Your Teeth" },
  { scene: "浴室，有瓷磚牆、蓮蓬頭和毛巾架，沒有浴缸", action: "坐在裝滿泡泡的浴缸裡洗澡", zh: "洗澡", en: "Take a Bath" },
  { scene: "浴室的洗手台，旁邊掛著毛巾", action: "雙手捧水洗臉", zh: "洗臉", en: "Wash Your Face" },
  { scene: "臥室的梳妝台，有鏡子", action: "一手拿梳子梳頭髮", zh: "梳頭髮", en: "Comb Your Hair" },
  { scene: "客廳，有沙發和茶几，茶几上有一盒面紙", action: "一手拿著面紙擦鼻子", zh: "擦鼻涕", en: "Wipe Your Nose" },
  { scene: "教室的座位旁，有書桌和窗戶", action: "用手肘摀住口鼻打噴嚏", zh: "摀住口鼻", en: "Cover Your Sneeze" },
  { scene: "公車站，有站牌和候車亭", action: "臉上戴著口罩，雙手把口罩邊緣按好", zh: "戴口罩", en: "Wear a Mask" },
  { scene: "臥室，有衣櫃和床", action: "手上拿著一件乾淨的衣服準備換上", zh: "換乾淨衣服", en: "Put On Clean Clothes" },
  { scene: "廚房的餐桌旁，桌上有水壺", action: "雙手捧著一杯水在喝", zh: "喝白開水", en: "Drink Water" },
];

/** 環遊世界：地標在背景（大建築放背景），主角站在前面，只拿相機、地圖這類小東西（拿在手上才不會被去背拿掉）。 */
const WORLD: readonly PackPage[] = [
  { scene: "法國巴黎的艾菲爾鐵塔，前面是一大片草地", action: "一手拿著相機在拍照，開心地笑", zh: "法國・艾菲爾鐵塔", en: "Eiffel Tower" },
  { scene: "美國紐約港口的自由女神像，遠處有海和雲", action: "高高舉起一隻手揮手", zh: "美國・自由女神像", en: "Statue of Liberty" },
  { scene: "中國的萬里長城，城牆在山上彎彎曲曲延伸", action: "背著小背包，一手拿著打開的地圖", zh: "中國・萬里長城", en: "Great Wall of China" },
  { scene: "埃及沙漠裡的三座金字塔，天上有太陽", action: "戴著遮陽帽，一手拿著水壺", zh: "埃及・金字塔", en: "Pyramids of Giza" },
  { scene: "埃及沙漠裡的人面獅身像，後面有金字塔", action: "一手拿著放大鏡在看", zh: "埃及・人面獅身像", en: "Great Sphinx" },
  { scene: "義大利的比薩斜塔，前面是草地", action: "雙手往旁邊伸，做出推著斜塔的姿勢", zh: "義大利・比薩斜塔", en: "Leaning Tower of Pisa" },
  { scene: "義大利羅馬的羅馬競技場，圓形的石頭建築有很多拱門", action: "一手拿著冰淇淋甜筒", zh: "義大利・羅馬競技場", en: "Colosseum" },
  { scene: "英國倫敦的大笨鐘鐘塔，旁邊是河", action: "一手拿著雨傘", zh: "英國・大笨鐘", en: "Big Ben" },
  { scene: "英國倫敦的倫敦塔橋，橋下是河", action: "雙手拿著一張明信片", zh: "英國・倫敦塔橋", en: "Tower Bridge" },
  { scene: "澳洲的雪梨歌劇院，屋頂像一片片白色的帆，旁邊是海港", action: "一手拿著相機在拍照", zh: "澳洲・雪梨歌劇院", en: "Sydney Opera House" },
  { scene: "印度的泰姬瑪哈陵，前面有長長的水池", action: "雙手合十微笑", zh: "印度・泰姬瑪哈陵", en: "Taj Mahal" },
  { scene: "日本的富士山，山頂有雪，前面有櫻花樹", action: "一手拿著一把小紙扇", zh: "日本・富士山", en: "Mount Fuji" },
  { scene: "台灣台北的台北101大樓，旁邊有其他大樓和樹", action: "高高舉起一隻手揮手", zh: "台灣・台北101", en: "Taipei 101" },
  { scene: "柬埔寨的吳哥窟寺廟，前面有水池", action: "一手拿著打開的地圖", zh: "柬埔寨・吳哥窟", en: "Angkor Wat" },
  { scene: "祕魯山上的馬丘比丘古城遺跡，後面有高高的山峰", action: "背著小背包，手拿登山杖", zh: "祕魯・馬丘比丘", en: "Machu Picchu" },
  { scene: "美國舊金山的金門大橋，橋下是海", action: "一手拿著相機在拍照", zh: "美國・金門大橋", en: "Golden Gate Bridge" },
  { scene: "西班牙巴塞隆納的聖家堂，有很多尖尖的高塔", action: "仰頭往上看，一手指著上面", zh: "西班牙・聖家堂", en: "Sagrada Família" },
  { scene: "德國柏林的布蘭登堡門，有很多大石柱", action: "雙手拿著一張明信片", zh: "德國・布蘭登堡門", en: "Brandenburg Gate" },
  { scene: "智利復活節島草地上的一排摩艾石像，後面是海", action: "一手拿著放大鏡在看", zh: "智利・摩艾石像", en: "Moai Statues" },
  { scene: "俄羅斯莫斯科的聖瓦西里大教堂，屋頂是洋蔥形的圓頂", action: "戴著毛線帽，雙手拿著一杯熱可可", zh: "俄羅斯・聖瓦西里大教堂", en: "St. Basil's Cathedral" },
];

export const THEME_PACKS: ThemePack[] = [
  {
    id: "vehicles",
    title: "交通工具",
    description: "10 種交通工具，每頁有場景、動作和中英文字",
    text: packText(VEHICLES, NO_VEHICLES),
  },
  {
    id: "jobs",
    title: "職業",
    description: "10 種職業，主角拿著那個職業的工具，每頁有中英文字",
    text: packText(JOBS, NO_PEOPLE),
  },
  {
    id: "hygiene",
    title: "衛生習慣",
    description: "10 個好習慣：洗手、刷牙、戴口罩……每頁有中英文字",
    text: packText(HYGIENE, NO_PEOPLE),
  },
  {
    id: "world",
    title: "環遊世界",
    description: "20 個世界知名地標，主角在地標前拍照、看地圖……每頁有中英文字",
    text: packText(WORLD, NO_PEOPLE),
  },
];
