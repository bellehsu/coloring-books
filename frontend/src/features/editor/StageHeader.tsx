"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import styles from "./StageHeader.module.css";

export type EditorMode = "sketch" | "line" | "qa";

export const MODES: { value: EditorMode; label: string }[] = [
  { value: "sketch", label: "構圖草圖" },
  { value: "line", label: "線稿與修線" },
  { value: "qa", label: "QA 檢查" },
];

export const tabIdOf = (prefix: string, m: EditorMode) => `${prefix}-tab-${m}`;

export interface StageHeaderProps {
  mode: EditorMode;
  onModeChange: (m: EditorMode) => void;
  /** 沒選圖時「線稿與修線」「QA 檢查」停用。 */
  hasSelection: boolean;
  /** QA 分頁的紅色數字（> 0 才顯示）。 */
  gapCount?: number;
  /** tab id 前綴（tabIdOf）與舞台 tabpanel 的 id。 */
  idPrefix: string;
  panelId: string;
  /** 右側插槽：修線模式放 <ZoomControls>。 */
  right?: ReactNode;
}

const DISABLED_HINT = "請先在下方候選圖中選一張";

export function StageHeader({ mode, onModeChange, hasSelection, gapCount = 0, idPrefix, panelId, right }: StageHeaderProps) {
  const hintId = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabled = (m: EditorMode) => m === "sketch" || hasSelection;

  function onKeyDown(e: KeyboardEvent, i: number) {
    const n = MODES.length;
    const order =
      e.key === "ArrowRight" || e.key === "ArrowDown"
        ? Array.from({ length: n }, (_, k) => (i + 1 + k) % n)
        : e.key === "ArrowLeft" || e.key === "ArrowUp"
          ? Array.from({ length: n }, (_, k) => (i - 1 - k + 2 * n) % n)
          : e.key === "Home"
            ? Array.from({ length: n }, (_, k) => k)
            : e.key === "End"
              ? Array.from({ length: n }, (_, k) => n - 1 - k)
              : null;
    if (!order) return;
    e.preventDefault();
    const j = order.find((k) => enabled(MODES[k].value));
    if (j === undefined) return;
    onModeChange(MODES[j].value);
    refs.current[j]?.focus();
  }

  return (
    <div className={styles.header}>
      <div role="tablist" aria-label="模式" className={styles.tabs}>
        {MODES.map((m, i) => {
          const on = m.value === mode;
          const ok = enabled(m.value);
          const badge = m.value === "qa" && hasSelection && gapCount > 0 ? gapCount : null;
          return (
            <button
              key={m.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              id={tabIdOf(idPrefix, m.value)}
              type="button"
              role="tab"
              aria-selected={on}
              aria-controls={panelId}
              aria-disabled={ok ? undefined : "true"}
              aria-describedby={ok ? undefined : hintId}
              title={ok ? undefined : DISABLED_HINT}
              tabIndex={on ? 0 : -1}
              className={styles.tab}
              data-on={on ? "true" : "false"}
              onClick={() => ok && !on && onModeChange(m.value)}
              onKeyDown={(e) => onKeyDown(e, i)}
            >
              {m.label}
              {badge !== null && (
                <span className={styles.badge}>
                  {badge}
                  <span className={styles.srOnly}> 個缺口</span>
                </span>
              )}
            </button>
          );
        })}
      </div>
      <span id={hintId} className={styles.srOnly}>
        {DISABLED_HINT}
      </span>
      {right && <div className={styles.right}>{right}</div>}
    </div>
  );
}
