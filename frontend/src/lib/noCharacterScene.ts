// 不放主角的頁：把場景文字改寫成「主體在畫面中間、畫得很大」。
// 規則與 backend/app/services/no_character.py 相同，兩邊共用 noCharacterScene.cases.json。
const BIG_PHRASE = "畫得很大，佔滿大半個畫面";

export function noCharacterScene(scene: string): string {
  const text = scene.replaceAll("畫面右邊", "畫面中間").replaceAll("畫面左邊", "畫面中間").replaceAll("小小的", "");
  const parts = text.split("，");
  const i = parts.findIndex((p) => p.includes("畫面中間"));
  if (i >= 0) parts.splice(i + 1, 0, BIG_PHRASE);
  return parts.join("，");
}
