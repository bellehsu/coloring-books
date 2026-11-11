"use client";

import { useId, useState } from "react";
import { countChars, MAX_CAPTION_EN, MAX_CAPTION_ZH, normalizeCaption } from "@/lib/limits";
import styles from "./CaptionField.module.css";

export type CaptionKind = "zh" | "en";

const SPEC: Record<CaptionKind, { label: string; max: number; unit: string; error: string }> = {
  zh: { label: "中文字", max: MAX_CAPTION_ZH, unit: "字", error: `中文字最多 ${MAX_CAPTION_ZH} 個字` },
  en: { label: "英文字", max: MAX_CAPTION_EN, unit: "字元", error: `英文字最多 ${MAX_CAPTION_EN} 個字元` },
};

/** 超過上限時回傳規格訊息（先正規化，再以 Unicode code point 計，與後端一致），否則 null。 */
export function captionError(kind: CaptionKind, value: string): string | null {
  return countChars(normalizeCaption(value)) > SPEC[kind].max ? SPEC[kind].error : null;
}

export interface CaptionFieldProps {
  kind: CaptionKind;
  /** 輸入框的無障礙名稱（可見標籤仍是「中文字」「英文字」）；沒給就用可見標籤。 */
  ariaLabel?: string;
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
}

/**
 * 圖下方的文字輸入。不用 maxLength（它以 UTF-16 計，會誤擋「𠮷」）：超過上限照樣可以輸入，
 * 但顯示訊息，呼叫端在超過時不存（captionError）。
 */
export function CaptionField({ kind, ariaLabel, value, onChange, onBlur }: CaptionFieldProps) {
  const id = useId();
  const { label, max, unit } = SPEC[kind];
  // 輸入法組字中（注音、拼音）內容還沒定案：不評估超過上限，組字結束才檢查
  const [composing, setComposing] = useState(false);
  const err = composing ? null : captionError(kind, value);
  const left = max - countChars(normalizeCaption(value));
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.lbl}>
        {label}
      </label>
      <input
        id={id}
        type="text"
        aria-label={ariaLabel}
        className={styles.input}
        value={value}
        aria-invalid={!!err}
        aria-describedby={`${id}-n`}
        lang={kind === "zh" ? "zh-Hant" : "en"}
        onChange={(e) => onChange(e.target.value)}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        onBlur={onBlur}
      />
      {/* 錯誤訊息放在一直存在的 polite 區域：出現時才會被朗讀，不用在出現當下才加 role="alert" */}
      <span id={`${id}-n`} className={styles.note}>
        {!err && <span className={styles.count}>{`還可 ${left} ${unit}`}</span>}
        <span aria-live="polite" className={styles.error}>
          {err}
        </span>
      </span>
    </div>
  );
}
