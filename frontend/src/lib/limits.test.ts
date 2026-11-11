import { describe, expect, it } from "vitest";
import { countChars, normalizeCaption } from "./limits";

describe("normalizeCaption（與後端 normalize_caption 同一套規則）", () => {
  it.each([
    ["消\n防", "消 防"],
    ["消\r\n\n防", "消 防"],
    ["消 防", "消 防"],
    ["消 防", "消 防"],
    ["消\u0085防", "消 防"],
    ["消\u001c防", "消 防"],
    ["消　 \t防", "消 防"],
    ["消 防", "消 防"],
    ["消​防", "消防"],
    ["消‍防", "消防"],
    ["﻿消防﻿", "消防"],
    ["消\u0000防\u001b", "消防"],
    ["  Fire   Engine ", "Fire Engine"],
    ["a \u0001 b", "a b"], // 控制字元夾在兩個空白之間：與後端一樣只留一個空白
    ["​", ""],
    [" \n ", ""],
    ["", ""],
  ])("%j → %j", (raw, want) => {
    expect(normalizeCaption(raw)).toBe(want);
  });

  it("正規化後才算字數（𠮷 算 1）", () => {
    expect(countChars(normalizeCaption("𠮷​\n𠮷"))).toBe(3);
  });
});
