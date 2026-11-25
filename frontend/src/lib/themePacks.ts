/** 內建主題包：用和「貼上整本故事」相同的格式寫成文字，套用時走同一個解析器。 */

import { DEFAULT_PLACEMENT, type Placement } from "@/api/client";
import { withSide, withSize } from "./placement";

export type LineDetail = "simple" | "standard" | "detailed";

/** 一頁的背景清理設定：auto＝新的 AI 背景好了自動清掉框外雜物；prompt＝自訂清理指示（沒有＝後端的預設）。 */
export interface PackCleanup {
  auto: boolean;
  prompt?: string;
}

export interface ThemePack {
  id: string;
  title: string;
  description: string;
  text: string;
  /** 用這個主題包建立／填入的頁，線條多寡設成這個（使用者 2026-11-19：小區塊太多 → 預設簡單）。 */
  lineDetail: LineDetail;
  /** 每頁主角的位置（依頁序）；沒有＝預設置中。套用時存成各頁的位置，之後在編輯頁可以改。 */
  placements?: Placement[];
  /** 每頁的背景清理設定（依頁序）；沒有＝不清。套用時和位置一樣存到各頁（auto_cleanup、cleanup_prompt）。 */
  cleanups?: PackCleanup[];
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

/** 地標類主題包：背景畫得簡單、大塊，線稿才不會有一堆小格子（使用者 2026-11-19）。 */
const SIMPLE_BG = "，背景簡單、大塊的形狀、細節少";

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
  { scene: "義大利的比薩斜塔，前面是草地", action: "雙手往右上方高高舉起、手掌張開，好像把旁邊快倒下來的東西撐住，開心地大笑", zh: "義大利・比薩斜塔", en: "Leaning Tower of Pisa" },
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

/** 台灣景點：同環遊世界的寫法（景點在背景，主角手上拿小東西；動作不提景點名稱，否則會在手上再畫一個）。 */
const TAIWAN: readonly PackPage[] = [
  { scene: "台北的故宮博物院，宮殿式的建築，前面有很長的階梯和廣場", action: "一手拿著導覽手冊，開心地笑", zh: "台北・故宮博物院", en: "National Palace Museum" },
  { scene: "台北的中正紀念堂：一座白色的大建築站在高高的台階上，屋頂是藍色的八角形尖屋頂，前面是一大片廣場", action: "一手比 YA，開心地笑", zh: "台北・中正紀念堂", en: "CKS Memorial Hall" },
  { scene: "台北的陽明山，山坡上開滿海芋和花，遠處有山", action: "一手拿著一朵小花聞香", zh: "台北・陽明山", en: "Yangmingshan" },
  { scene: "新北的九份老街，山坡上的窄階梯兩旁掛著紅燈籠，有木造的茶樓", action: "雙手捧著一碗芋圓", zh: "新北・九份老街", en: "Jiufen Old Street" },
  { scene: "新北平溪的老街和鐵軌，夜空中飄著很多天燈", action: "雙手捧著一盞紙做的大燈籠往上放", zh: "新北・平溪天燈", en: "Pingxi Sky Lanterns" },
  { scene: "新北淡水漁人碼頭的情人橋：一座白色的人行橋橫跨河面，橋中間立著一根高高斜斜的白色柱子，很多條鋼索從柱子拉到橋上，後面是夕陽和海", action: "高高舉起一隻手揮手", zh: "新北・淡水漁人碼頭", en: "Tamsui Fisherman's Wharf" },
  { scene: "苗栗的勝興車站，日式的木造小車站，旁邊有鐵軌", action: "雙手拿著一張車票", zh: "苗栗・勝興車站", en: "Shengxing Station" },
  { scene: "台中的高美濕地，一排大風車，地上有淺淺的水和夕陽", action: "一手拿著一個小風車玩具", zh: "台中・高美濕地", en: "Gaomei Wetlands" },
  { scene: "彰化八卦山上的大佛像，前面有階梯和樹", action: "雙手合十微笑", zh: "彰化・八卦山大佛", en: "Baguashan Great Buddha" },
  { scene: "南投的清境農場，一大片綠色的草原，草地上有幾隻白白胖胖的綿羊，遠處是山", action: "蹲下來，一手拿著一把青草", zh: "南投・清境農場", en: "Cingjing Farm" },
  { scene: "台南的赤崁樓，紅磚牆和中式屋頂的古樓，前面有石碑", action: "一手拿著打開的地圖", zh: "台南・赤崁樓", en: "Chihkan Tower" },
  { scene: "台南的安平古堡，紅磚城牆上有白色的瞭望塔", action: "一手拿著一面小旗子", zh: "台南・安平古堡", en: "Anping Fort" },
  { scene: "台南的孔廟，紅色牆壁和黑色屋瓦的古老廟宇，屋頂兩邊翹起來，前面有一棵大榕樹", action: "雙手捧著一本打開的書", zh: "台南・孔廟", en: "Tainan Confucius Temple" },
  { scene: "高雄蓮池潭的龍虎塔，兩座七層的塔立在湖上，前面有曲折的橋", action: "雙手拿著一張明信片", zh: "高雄・蓮池潭龍虎塔", en: "Dragon and Tiger Pagodas" },
  { scene: "屏東墾丁的鵝鑾鼻燈塔，白色的燈塔在草地上，旁邊是海", action: "戴著遮陽帽，一手拿著一杯椰子汁", zh: "屏東・鵝鑾鼻燈塔", en: "Eluanbi Lighthouse" },
  { scene: "屏東墾丁的海邊，畫面右邊的海裡有一塊很大的岩石，上面尖尖、下面寬寬，是三角形的，旁邊是沙灘和椰子樹", action: "戴著草帽，一手拿著一個貝殼", zh: "屏東・墾丁帆船石", en: "Sail Rock, Kenting" },
  { scene: "花蓮的太魯閣峽谷，高高的大理石山壁中間有溪流和吊橋", action: "戴著安全帽，一手扶著帽子往上看", zh: "花蓮・太魯閣", en: "Taroko Gorge" },
  { scene: "花蓮的七星潭，彎彎的海岸，岸邊鋪滿圓圓的鵝卵石，遠處有高高的山，海浪拍上岸", action: "蹲下來，一手撿起一顆圓圓的小石頭", zh: "花蓮・七星潭", en: "Qixingtan Beach" },
  { scene: "台東池上的伯朗大道，一條筆直的小路穿過一大片稻田，遠處是高高的山和白雲", action: "騎著腳踏車，一手揮手", zh: "台東・伯朗大道", en: "Mr. Brown Avenue" },
  { scene: "台東的三仙台，海上一座像波浪一樣彎彎的長橋連到小島", action: "一手拿著貝殼", zh: "台東・三仙台", en: "Sanxiantai Bridge" },
  { scene: "澎湖七美的雙心石滬：從海邊高處往下看，淺淺的海水裡，用黑色石塊疊成的矮石牆排成兩個大大的愛心，兩個愛心相連，一大一小，旁邊是綠色的草坡", action: "一手拿著相機在拍照", zh: "澎湖・雙心石滬", en: "Twin Hearts Stone Weir" },
  { scene: "金門的風獅爺石像，披著紅色披風站在路邊，後面有閩南式的老房子", action: "一手拿著一個小紙風車", zh: "金門・風獅爺", en: "Wind Lion God" },
];

/** 節日：場景是節日的地方，主角拿節日的小東西（使用者 2026-11-19 確認清單）。 */
const HOLIDAYS: readonly PackPage[] = [
  { scene: "晚上的城市，天空中只有三朵大大的煙火，天空其他地方是空的，沒有星星和雲", action: "雙手舉高，開心地歡呼", zh: "新年快樂", en: "Happy New Year" },
  { scene: "家門口，門兩邊貼著長長的紅紙，紅紙上沒有字，門上掛著兩個紅燈籠", action: "雙手拿著一個紅包，開心地笑", zh: "過年", en: "Lunar New Year" },
  { scene: "晚上的廟口廣場，上面掛著一排圓圓的燈籠", action: "一手提著一盞小燈籠", zh: "元宵節", en: "Lantern Festival" },
  { scene: "遊樂園，有旋轉木馬和摩天輪", action: "一手拿著一顆氣球", zh: "兒童節", en: "Children's Day" },
  { scene: "客廳，有沙發和窗戶，窗外有陽光", action: "雙手捧著一束康乃馨", zh: "母親節", en: "Mother's Day" },
  { scene: "河邊，水面平靜，岸邊有幾棵大樹", action: "坐在龍舟上划槳，龍舟長長的，船頭是龍頭", zh: "端午節", en: "Dragon Boat Festival" },
  { scene: "客廳，有一張大沙發和一盞立燈", action: "雙手拿著一張自己畫的卡片", zh: "父親節", en: "Father's Day" },
  { scene: "晚上的院子，天上有一個又大又圓的月亮", action: "雙手捧著一個圓圓的月餅", zh: "中秋節", en: "Mid-Autumn Festival" },
  { scene: "教室，有黑板和一排課桌", action: "雙手拿著一張謝卡", zh: "教師節", en: "Teachers' Day" },
  { scene: "晚上的家門口，門邊放著幾個南瓜燈", action: "一手提著一個南瓜造型的糖果籃", zh: "萬聖節", en: "Halloween" },
  { scene: "客廳，有一棵大聖誕樹，樹下放著禮物", action: "戴著聖誕帽，雙手抱著一個禮物盒", zh: "聖誕節", en: "Christmas" },
  { scene: "客廳，牆上掛著彩色三角旗和氣球", action: "雙手捧著一個插著蠟燭的生日蛋糕", zh: "生日快樂", en: "Happy Birthday" },
];

/** 動物園：動物在背景；動作不提動物（否則會在主角手上再畫一隻）。 */
const ZOO: readonly PackPage[] = [
  { scene: "動物園裡，畫面右邊有一隻高高的長頸鹿站在大樹旁邊吃葉子", action: "一手指著上面，開心地笑", zh: "長頸鹿", en: "Giraffe" },
  { scene: "動物園裡，畫面左邊有一隻大大的大象站在水池邊，用長鼻子噴水", action: "雙手撐著一把小雨傘", zh: "大象", en: "Elephant" },
  { scene: "動物園裡，畫面右邊有一隻有大鬃毛的獅子趴在大石頭上", action: "雙手拿著望遠鏡在看", zh: "獅子", en: "Lion" },
  { scene: "動物園裡，畫面左邊有一隻大大的貓熊坐在竹林裡吃竹子", action: "一手拿著一片竹葉", zh: "貓熊", en: "Giant Panda" },
  { scene: "動物園裡，畫面右邊的大樹上有幾隻猴子在盪來盪去", action: "雙手捧著一串香蕉", zh: "猴子", en: "Monkeys" },
  { scene: "動物園裡，畫面左邊有幾隻企鵝站在大冰塊上，旁邊是水池", action: "戴著毛線帽，一手揮手", zh: "企鵝", en: "Penguins" },
  { scene: "動物園裡，畫面右邊有兩隻斑馬站在草地上，後面有矮樹", action: "一手拿著相機在拍照", zh: "斑馬", en: "Zebras" },
  { scene: "動物園裡，畫面左邊有一隻大大的河馬在泥水池裡張大嘴巴", action: "雙手摀著嘴巴偷笑", zh: "河馬", en: "Hippo" },
  { scene: "動物園裡，畫面右邊有一隻無尾熊抱著大樹的樹幹睡覺", action: "一根手指放在嘴巴前面，小聲說噓", zh: "無尾熊", en: "Koala" },
  { scene: "動物園裡，畫面左邊有一隻大大的袋鼠站在草地上，肚子的袋子裡有一隻小袋鼠", action: "雙腳跳起來，開心地笑", zh: "袋鼠", en: "Kangaroo" },
  { scene: "動物園裡，畫面右邊有一群紅鶴單腳站在淺淺的水裡", action: "單腳站立，雙手張開保持平衡", zh: "紅鶴", en: "Flamingos" },
  { scene: "動物園裡，畫面左邊有一隻大大的鱷魚趴在河邊，嘴巴張開", action: "一手拿著一根冰棒", zh: "鱷魚", en: "Crocodile" },
];

/** 運動：場地在背景，主角拿器材（使用者 2026-11-19 換掉籃球、桌球、羽球、網球、游泳）。 */
const SPORTS: readonly PackPage[] = [
  { scene: "室內溜冰場，冰面平平亮亮的，四周有矮矮的圍欄", action: "穿著溜冰鞋，雙手張開在冰上滑", zh: "溜冰", en: "Ice Skating" },
  { scene: "足球場的草地，後面有一個大球門", action: "一腳踢著一顆足球", zh: "足球", en: "Soccer" },
  { scene: "棒球場，有本壘板和後面的鐵絲網", action: "戴著打擊頭盔，雙手握著球棒準備揮棒", zh: "棒球", en: "Baseball" },
  { scene: "高爾夫球場的綠色草地，遠處有一面插在洞裡的小旗子", action: "雙手握著一根高爾夫球桿準備揮桿", zh: "高爾夫球", en: "Golf" },
  { scene: "沙灘排球場，中間有一面高高的球網，後面是海", action: "雙手把一顆排球往上托", zh: "排球", en: "Volleyball" },
  { scene: "美式足球場的草地，有一條一條的白線，遠處有高高的球門柱", action: "戴著美式足球頭盔，一手抱著一顆橢圓形的美式足球", zh: "美式足球", en: "American Football" },
  { scene: "室內，有一張大大的撞球桌，桌上有幾顆球", action: "雙手拿著一根長長的撞球桿", zh: "撞球", en: "Billiards" },
  { scene: "學校操場，後面有一排大樹", action: "雙手拿著跳繩跳起來", zh: "跳繩", en: "Jump Rope" },
  { scene: "操場的跑道，有一條一條的白線", action: "往前跑步，頭上綁著頭帶", zh: "跑步", en: "Running" },
  { scene: "公園裡平平的廣場，旁邊有大樹", action: "踩在滑板上，雙手張開保持平衡", zh: "滑板", en: "Skateboarding" },
  { scene: "室內體育館，地上鋪著一大塊平平的軟墊", action: "雙手撐在一個鞍馬的兩個把手上，雙腳往旁邊抬起", zh: "體操", en: "Gymnastics" },
  { scene: "山上的步道，遠處有一座一座的山", action: "背著背包，一手拿著登山杖", zh: "登山", en: "Hiking" },
];

/** 華麗相框：背景是古典捲草花紋外框、中間空白，主角站在框裡（使用者 2026-11-24）。 */
const FRAME_STYLE = "，只有黑色線條、沒有塗黑的地方";
// 驗收（2026-11-24）：框小的時候 Qwen 會在旁邊畫一堆東西 → 框要幾乎佔滿整張紙、明說沒有其他東西；
// 「空白」會被畫成透明格子 → 說「乾淨的白色」；「四周留白邊」會多畫一圈方框（使用者不要）；
// 寫出「沒有地面、草叢或雲」反而每頁都畫出草地和雲 → 不點名要排除的東西（措辭實驗 wording_AB.png，A 版）。
const frame = (shape: string, extra = "") =>
  `白色背景，一個很大的${shape}古典捲草花紋外框${extra}，外框幾乎佔滿整張紙；` +
  `框裡面是乾淨的白色，什麼都沒有；除了這個外框，畫面裡沒有任何其他東西`;

const FRAMES: readonly PackPage[] = [
  { scene: frame("直立橢圓形"), action: "一手揮手，開心地微笑", zh: "橢圓形", en: "Oval" },
  { scene: frame("直立長方形"), action: "雙手放在背後，站得直直的，微笑", zh: "長方形", en: "Rectangle" },
  { scene: frame("圓形", "，整個圓形外框都在紙張裡面，沒有被切掉"), action: "雙手托著臉頰，開心地笑", zh: "圓形", en: "Circle" },
  { scene: frame("空心四葉草形", "，外框就是四葉草的輪廓線，用捲草花紋畫成，四葉草裡面是乾淨的白色"), action: "雙手在胸前比出一個愛心", zh: "四葉草", en: "Four-Leaf Clover" },
  { scene: frame("直立六角形", "，外框的最上面和最下面是尖角，左右兩邊是直的，總共六條邊"), action: "一手舉著一根星星魔法棒", zh: "六角形", en: "Hexagon" },
  { scene: frame("直立橢圓形花環", "，外框上有花朵和葉子"), action: "雙手捧著一束花", zh: "花環", en: "Wreath" },
  { scene: frame("拱門形", "，上面是圓弧、兩邊是直的"), action: "張開雙手歡迎", zh: "拱門", en: "Arch" },
  { scene: frame("盾牌形"), action: "一手叉腰，一手比讚", zh: "盾牌", en: "Shield" },
  { scene: frame("直立方形", "，四個角是圓角"), action: "雙手抱著一隻泰迪熊", zh: "圓角方形", en: "Rounded Square" },
  { scene: frame("直立長方形", "，外框上方正中間有一個大蝴蝶結"), action: "雙手捧著一個禮物盒", zh: "蝴蝶結", en: "Bow" },
  { scene: frame("直立橢圓形", "，外框頂上有一頂皇冠"), action: "頭上戴著一頂小皇冠，雙手合在胸前微笑", zh: "皇冠", en: "Crown" },
  { scene: frame("菱形"), action: "一手比 V，開心地笑", zh: "菱形", en: "Diamond" },
];

// v2（使用者 2026-11-24）：星星、愛心、雲朵常多包方框 → 換四葉草（帶方框也可以）、六角形、圓角方形；肖像模式預設開。
/** 華麗相框的主角位置：半身肖像放進框裡，frame 是框的中心，autofit 讓後端依框自動縮放。 */
const FRAME_PLACEMENT: Placement = { cx: 0.5, cy: 0.59, height: 0.42, flip: false, frame: { x: 0.5, y: 0.55 }, autofit: true };

/** 四葉草頁的清理指示（spec 2026-11-24 §5.3）：四葉草框外常多一圈長方形花邊，要整圈拿掉。 */
const CLOVER_CLEANUP =
  "Delete the rectangular decorative border that runs along the four edges of the picture, including its corner ornaments. " +
  "Only the four-leaf clover shaped frame in the middle remains, unchanged. The rest of the page is plain pure white. Black and white line art.";

/** 華麗相框每頁都自動清掉框外雜物；第 4 頁（四葉草）用自己的指示，其他用後端的預設。 */
const FRAME_CLEANUPS: PackCleanup[] = FRAMES.map((p) => (p.zh === "四葉草" ? { auto: true, prompt: CLOVER_CLEANUP } : { auto: true }));

/** 恐龍（使用者 2026-11-25）：恐龍在背景、左右輪流；動作不提恐龍（否則手上會再畫一隻）。 */
const DINOS: readonly PackPage[] = [
  { scene: "史前時代的森林，畫面右邊有一隻大大的暴龍張大嘴巴，後面有大樹", action: "戴著探險帽，雙手拿著望遠鏡在看", zh: "暴龍", en: "T-Rex" },
  { scene: "史前時代的草原，畫面左邊有一隻三角龍低頭吃草", action: "一手拿著一把葉子往前伸", zh: "三角龍", en: "Triceratops" },
  { scene: "史前時代的森林，畫面右邊有一隻背上有一排骨板的劍龍", action: "揹著背包，一手揮手", zh: "劍龍", en: "Stegosaurus" },
  { scene: "史前時代的森林，畫面左邊有一隻脖子很長的腕龍在吃高高的樹葉", action: "抬頭往上看，一手指著上面", zh: "腕龍", en: "Brachiosaurus" },
  { scene: "史前時代的山谷，畫面右邊的天空有一隻張開翅膀的翼龍在飛，遠處有山，沒有星星和雲", action: "雙手張開，假裝在飛", zh: "翼龍", en: "Pterosaur" },
  { scene: "史前時代的草地，畫面左邊有一隻尾巴末端有骨錘的甲龍", action: "蹲下來，好奇地看", zh: "甲龍", en: "Ankylosaurus" },
  { scene: "史前時代的森林，畫面右邊有一隻小小的迅猛龍在奔跑", action: "往前跑步", zh: "迅猛龍", en: "Velociraptor" },
  { scene: "史前時代的河邊，畫面左邊的河裡有一隻背上有大帆的棘龍", action: "雙手拿著一根釣竿", zh: "棘龍", en: "Spinosaurus" },
  { scene: "史前時代的森林，畫面右邊有一隻頭上有長長頭冠的副櫛龍", action: "雙手摀著耳朵，開心地笑", zh: "副櫛龍", en: "Parasaurolophus" },
  { scene: "史前時代的草地，畫面左邊有一隻頭頂圓圓厚厚的厚頭龍", action: "雙手抱著一顆大球", zh: "厚頭龍", en: "Pachycephalosaurus" },
  { scene: "史前時代的湖邊，畫面右邊的湖裡有一隻探出長脖子的蛇頸龍", action: "坐在小船上，雙手划槳", zh: "蛇頸龍", en: "Plesiosaur" },
  { scene: "史前時代的草地，畫面左邊的草叢裡有一個大大的巢，裡面有幾顆大大的蛋", action: "一根手指放在嘴巴前面，小聲說噓", zh: "恐龍蛋", en: "Dinosaur Eggs" },
];

/** 海洋動物（使用者 2026-11-25）：海底場景、動物左右輪流；不畫泡泡（線稿會變成細碎點點）。 */
const OCEAN: readonly PackPage[] = [
  { scene: "海底，有海草和大石頭，畫面右邊有一隻大大的鯨魚", action: "戴著潛水面鏡，一手揮手", zh: "鯨魚", en: "Whale" },
  { scene: "海底，有海草和大石頭，畫面左邊有一隻海豚往上游", action: "雙手往前划水", zh: "海豚", en: "Dolphin" },
  { scene: "海底，有海草，畫面右邊的大石頭上有一隻章魚", action: "一手拿著一個小網子", zh: "章魚", en: "Octopus" },
  { scene: "海底，有海草和大石頭，畫面左邊有一隻大大的海龜在游", action: "雙手張開，在水裡游泳", zh: "海龜", en: "Sea Turtle" },
  { scene: "海底，有海草和大石頭，畫面右邊遠處有一隻鯊魚", action: "雙手拿著望遠鏡在看", zh: "鯊魚", en: "Shark" },
  { scene: "海底，畫面左邊有一大叢海葵，海葵裡有幾隻小丑魚", action: "蹲下來，好奇地看", zh: "小丑魚", en: "Clownfish" },
  { scene: "海底，有海草，畫面右邊有幾隻水母飄著", action: "一手指著上面，開心地笑", zh: "水母", en: "Jellyfish" },
  { scene: "海底的沙地，有海草，畫面左邊的沙地上有幾隻大大的海星", action: "一手拿著一把小鏟子", zh: "海星", en: "Starfish" },
  { scene: "海底的沙地，有海草，畫面右邊的沙地上有一隻舉著大鉗子的螃蟹", action: "雙手摀著嘴巴偷笑", zh: "螃蟹", en: "Crab" },
  { scene: "海底，畫面左邊的海草旁有兩隻海馬", action: "一手拿著放大鏡在看", zh: "海馬", en: "Seahorse" },
  { scene: "海底的沙地，畫面右邊有一隻扁扁的魟魚在沙地上方游", action: "揹著潛水氧氣瓶，一手揮手", zh: "魟魚", en: "Stingray" },
  { scene: "海邊，畫面左邊的大石頭上有一隻海獅，後面是海", action: "雙手拍手", zh: "海獅", en: "Sea Lion" },
];

/** 交通安全（使用者 2026-11-25）：背景沒有車也沒有人，車輛寫在動作（主角坐在上面）；黑白看不出紅綠燈亮哪個，合成一頁。 */
const SAFETY: readonly PackPage[] = [
  { scene: "城市的馬路，路面上有一條大大的斑馬線", action: "舉起一隻手，往前走", zh: "走斑馬線", en: "Use the Crosswalk" },
  { scene: "城市的路口，路邊有一個大大的紅綠燈", action: "停下來，一手指著前面", zh: "看紅綠燈", en: "Watch the Traffic Light" },
  { scene: "安靜的十字路口，路邊有大樹", action: "站著轉頭看旁邊, facing toward the left side of the picture", zh: "左右看", en: "Look Both Ways" },
  { scene: "人行道，旁邊有大樹和路燈", action: "揹著書包走路", zh: "走人行道", en: "Walk on the Sidewalk" },
  { scene: "停車場的空地，旁邊有大樹", action: "坐在兒童安全座椅上，雙手拉著安全帶", zh: "繫安全帶", en: "Buckle Up" },
  { scene: "公園裡的腳踏車道，旁邊有大樹", action: "戴著安全帽，騎腳踏車", zh: "戴安全帽", en: "Wear a Helmet" },
  { scene: "傍晚的路邊，有路燈和大樹", action: "穿著亮亮的反光背心，一手揮手", zh: "穿反光背心", en: "Wear a Reflective Vest" },
  { scene: "公車站，有站牌和候車亭", action: "揹著書包，站好排隊", zh: "排隊等公車", en: "Wait in Line" },
  { scene: "公車站旁邊，有站牌和大樹", action: "坐在公車座位上，雙手放在腿上", zh: "坐好扶好", en: "Sit Still on the Bus" },
  { scene: "晚上的小路，路邊有路燈，沒有星星和雲", action: "一手拿著手電筒往前照", zh: "帶手電筒", en: "Carry a Flashlight" },
  { scene: "平交道，柵欄放下來，旁邊有一個警示牌", action: "停下來，雙手放在身體兩旁等待", zh: "平交道停看聽", en: "Stop at the Rail Crossing" },
  { scene: "公園的大草地，旁邊有大樹", action: "雙手抱著一顆球", zh: "在公園玩", en: "Play in the Park" },
];

/** 動物在畫面右邊（單數頁）就把主角放左邊，反之放右邊，主角小一點（同動物園）。 */
const oppositeSmall = (pages: readonly PackPage[]) =>
  pages.map((_, i) => withSide(withSize(DEFAULT_PLACEMENT, "small"), i % 2 === 0 ? "left" : "right"));

export const THEME_PACKS: ThemePack[] = [
  {
    id: "vehicles",
    title: "交通工具",
    description: "10 種交通工具，每頁有場景、動作和中英文字",
    text: packText(VEHICLES, NO_VEHICLES),
    lineDetail: "simple",
  },
  {
    id: "jobs",
    title: "職業",
    description: "10 種職業，主角拿著那個職業的工具，每頁有中英文字",
    text: packText(JOBS, NO_PEOPLE),
    lineDetail: "simple",
  },
  {
    id: "hygiene",
    title: "衛生習慣",
    description: "10 個好習慣：洗手、刷牙、戴口罩……每頁有中英文字",
    text: packText(HYGIENE, NO_PEOPLE),
    lineDetail: "simple",
  },
  {
    id: "world",
    title: "環遊世界",
    description: "20 個世界知名地標，主角在地標前拍照、看地圖……每頁有中英文字",
    text: packText(WORLD, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
  },
  {
    id: "taiwan",
    title: "台灣景點",
    description: "22 個台灣的知名景點，從北到南、到離島，每頁有中英文字",
    text: packText(TAIWAN, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
  },
  {
    id: "holidays",
    title: "節日",
    description: "12 個節日：過年、端午、中秋、聖誕、生日……主角拿著節日的小東西，每頁有中英文字",
    text: packText(HOLIDAYS, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
  },
  {
    id: "zoo",
    title: "動物園",
    description: "12 種動物園的動物，主角在旁邊看、拍照……每頁有中英文字",
    text: packText(ZOO, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
    // 使用者 2026-11-20：主角小一點、不放中間；動物在右邊（單數頁）就放左邊，反之放右邊
    placements: ZOO.map((_, i) => withSide(withSize(DEFAULT_PLACEMENT, "small"), i % 2 === 0 ? "left" : "right")),
  },
  {
    id: "sports",
    title: "運動",
    description: "12 種運動：溜冰、足球、棒球、排球、跳繩……主角拿著器材，每頁有中英文字",
    text: packText(SPORTS, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
  },
  {
    id: "frames",
    title: "華麗相框",
    description: "12 種形狀的古典花紋相框，主角是框裡的半身肖像，每頁有中英文字",
    text: packText(FRAMES, FRAME_STYLE + NO_PEOPLE),
    // 使用者 2026-11-24：複雜度中等
    lineDetail: "standard",
    placements: FRAMES.map(() => FRAME_PLACEMENT),
    cleanups: FRAME_CLEANUPS,
  },
  {
    id: "dinosaurs",
    title: "恐龍",
    description: "12 種恐龍：暴龍、三角龍、劍龍……主角在旁邊探險，每頁有中英文字",
    text: packText(DINOS, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
    placements: oppositeSmall(DINOS),
  },
  {
    id: "ocean",
    title: "海洋動物",
    description: "12 種海洋動物：鯨魚、海豚、章魚、海龜……主角在海底潛水，每頁有中英文字",
    text: packText(OCEAN, SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
    placements: oppositeSmall(OCEAN),
  },
  {
    id: "safety",
    title: "交通安全",
    description: "12 個交通安全好習慣：走斑馬線、看紅綠燈、繫安全帶……每頁有中英文字",
    text: packText(SAFETY, NO_VEHICLES + SIMPLE_BG + NO_PEOPLE),
    lineDetail: "simple",
  },
];
