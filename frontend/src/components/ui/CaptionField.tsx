"use client";

import { useId } from "react";
import { countChars, MAX_CAPTION_EN, MAX_CAPTION_ZH } from "@/lib/limits";
import styles from "./CaptionField.module.css";

export type CaptionKind = "zh" | "en";

const SPEC: Record<CaptionKind, { label: string; max: number; unit: string; error: string }> = {
  zh: { label: "中文字", max: MAX_CAPTION_ZH, unit: "字", error: `中文字最多 ${MAX_CAPTION_ZH} 個字` },
  en: { label: "英文字", max: MAX_CAPTION_EN, unit: "字元", error: `英文字最多 ${MAX_CAPTION_EN} 個字元` },
};

/** 超過上限時回傳規格訊息（以 Unicode code point 計，與後端一致），否則 null。 */
export function captionError(kind: CaptionKind, value: string): string | null {
  return countChars(value.trim()) > SPEC[kind].max ? SPEC[kind].error : null;
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
  const err = captionError(kind, value);
  const left = max - countChars(value.trim());
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
        onBlur={onBlur}
      />
      <span id={`${id}-n`} className={err ? styles.error : styles.count} role={err ? "alert" : undefined}>
        {err ?? `還可 ${left} ${unit}`}
      </span>
    </div>
  );
}
