"use client";

import { useRef, type KeyboardEvent } from "react";
import styles from "./Segmented.module.css";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  badge?: number | string;
  /** 停用：不能點，方向鍵會跳過。 */
  disabled?: boolean;
}

export interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (v: T) => void;
  /** tabs → role=tablist/tab；radio → radiogroup/radio */
  kind?: "tabs" | "radio";
  label: string;
  /** 群組的說明（aria-describedby）。 */
  describedBy?: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  kind = "tabs",
  label,
  describedBy,
}: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const tabs = kind === "tabs";
  // 選中的那個被停用（或沒有選中）時，讓第一個可用的留在 tab 順序裡
  const selected = options.findIndex((o) => o.value === value && !o.disabled);
  const focusable = selected >= 0 ? selected : options.findIndex((o) => !o.disabled);

  /** 從 i 往 step 方向找下一個可用的（繞一圈都沒有 → -1）。 */
  function nextEnabled(i: number, step: 1 | -1) {
    const n = options.length;
    for (let k = 1; k <= n; k++) {
      const j = (((i + step * k) % n) + n) % n;
      if (!options[j].disabled) return j;
    }
    return -1;
  }

  function onKeyDown(e: KeyboardEvent, i: number) {
    const n = options.length;
    let j = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") j = nextEnabled(i, 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = nextEnabled(i, -1);
    else if (e.key === "Home") j = nextEnabled(n - 1, 1);
    else if (e.key === "End") j = nextEnabled(0, -1);
    if (j < 0) return;
    e.preventDefault();
    if (options[j].value !== value) onChange(options[j].value);
    refs.current[j]?.focus();
  }

  return (
    <div
      role={tabs ? "tablist" : "radiogroup"}
      aria-label={label}
      aria-describedby={describedBy}
      className={styles.group}
    >
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role={tabs ? "tab" : "radio"}
            {...(tabs ? { "aria-selected": on } : { "aria-checked": on })}
            tabIndex={i === focusable ? 0 : -1}
            disabled={o.disabled}
            className={styles.item}
            data-on={on ? "true" : "false"}
            onClick={() => {
              if (!on) onChange(o.value);
            }}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            {o.label}
            {o.badge !== undefined && o.badge !== 0 && <span className={styles.badge}>{o.badge}</span>}
          </button>
        );
      })}
    </div>
  );
}
