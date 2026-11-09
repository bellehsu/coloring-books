"use client";

import { useRef, type KeyboardEvent } from "react";
import styles from "./Segmented.module.css";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  badge?: number | string;
}

export interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (v: T) => void;
  /** tabs → role=tablist/tab；radio → radiogroup/radio */
  kind?: "tabs" | "radio";
  label: string;
}

export function Segmented<T extends string>({ options, value, onChange, kind = "tabs", label }: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const tabs = kind === "tabs";

  function onKeyDown(e: KeyboardEvent, i: number) {
    const n = options.length;
    let j = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % n;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + n) % n;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = n - 1;
    if (j < 0) return;
    e.preventDefault();
    onChange(options[j].value);
    refs.current[j]?.focus();
  }

  return (
    <div role={tabs ? "tablist" : "radiogroup"} aria-label={label} className={styles.group}>
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
            tabIndex={on ? 0 : -1}
            className={styles.item}
            data-on={on ? "true" : "false"}
            onClick={() => onChange(o.value)}
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
