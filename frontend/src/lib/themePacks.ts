/** 內建主題包：用和「貼上整本故事」相同的格式寫成文字，套用時走同一個解析器。 */

export interface ThemePack {
  id: string;
  title: string;
  description: string;
  text: string;
}

/** 每頁場景的結尾：避免背景圖裡出現交通工具。 */
const NO_VEHICLES = "，沒有任何車輛、船、飛機或火車";

const VEHICLES: readonly { scene: string; action: string; zh: string; en: string }[] = [
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

const vehiclesText = VEHICLES.map(
  (v, i) => `第${i + 1}頁\n場景：${v.scene}${NO_VEHICLES}\n動作：${v.action}\n中文：${v.zh}\n英文：${v.en}`,
).join("\n\n");

export const THEME_PACKS: ThemePack[] = [
  {
    id: "vehicles",
    title: "交通工具",
    description: "10 種交通工具，每頁有場景、動作和中英文字",
    text: vehiclesText,
  },
];
