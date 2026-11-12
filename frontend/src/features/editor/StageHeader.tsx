"use client";

import Link from "next/link";
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import styles from "./StageHeader.module.css";

export type EditorMode = "compose" | "line";

export const MODES: { value: EditorMode; label: string }[] = [
  { value: "compose", label: "① 構圖" },
  { value: "line", label: "② 補線" },
];

export const DEFAULT_MODE: EditorMode = "compose";

export const isEditorMode = (v: string | null): v is EditorMode => MODES.some((m) => m.value === v);

/** 網址的 mode 參數 → 編輯器模式；未知值一律回構圖。 */
export const normalizeMode = (v: string | null): EditorMode => (isEditorMode(v) ? v : DEFAULT_MODE);

/** C 改過、頁面流程還在重新合成時的說明（補線停用的原因，也顯示在舞台上方）。 */
export const RECOMPOSING_NOTICE = "這一頁正在重新合成…好了會自動更新";
/** 重新合成失敗（還等著、但流程不會再跑）：要到 C 按「再試一次」。 */
export const RECOMPOSE_FAILED_NOTICE = "重新合成沒成功，請到處理頁面按「再試一次」";

/** 決定分頁能不能用的頁面狀態。 */
export interface ModeGate {
  selectedId: number | null;
  /** page.recompose_pending：選定圖還是舊的合成圖，補的線會在流程換圖時不見。 */
  recomposing?: boolean;
  /** 重新合成失敗（recompose_pending＋attention）。 */
  recomposeFailed?: boolean;
}

/**
 * 停用的分頁 → 原因（顯示在 tooltip 與 aria-describedby）；不在表內＝可用。
 * 構圖永遠可用；補線要有選定圖（合成圖，或舊流程頁面的圖），而且這頁沒有在等重新合成。
 */
export function modeBlockers(g: ModeGate): Partial<Record<EditorMode, string>> {
  if (g.selectedId == null) return { line: "請先擺好角色位置" };
  if (!g.recomposing) return {};
  return { line: g.recomposeFailed ? RECOMPOSE_FAILED_NOTICE : RECOMPOSING_NOTICE };
}

/** 目標模式能用就用它，否則回到構圖。 */
export const allowedMode = (m: EditorMode, g: ModeGate): EditorMode => (modeBlockers(g)[m] ? DEFAULT_MODE : m);

export const tabIdOf = (prefix: string, m: EditorMode) => `${prefix}-tab-${m}`;

export interface StageHeaderProps {
  mode: EditorMode;
  onModeChange: (m: EditorMode) => void;
  /** 停用的分頁 → 原因（modeBlockers）。 */
  blockers: Partial<Record<EditorMode, string>>;
  /** 補線分頁的紅色數字（缺口數，> 0 才顯示）。 */
  gapCount?: number;
  /** tab id 前綴（tabIdOf）與舞台 tabpanel 的 id。 */
  idPrefix: string;
  panelId: string;
  /** 右側插槽：修線模式放 <ZoomControls>。 */
  right?: ReactNode;
  /** 分頁旁的說明（例如 RECOMPOSING_NOTICE）。 */
  notice?: ReactNode;
  /** 有給：顯示「回到處理這一頁」連結（目前頁的 C）。 */
  backHref?: string;
}

export function StageHeader({ mode, onModeChange, blockers, gapCount = 0, idPrefix, panelId, right, notice, backHref }: StageHeaderProps) {
  const hintId = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabled = (m: EditorMode) => !blockers[m];
  const hintOf = (m: EditorMode) => `${hintId}-${m}`;

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
          const badge = m.value === "line" && ok && gapCount > 0 ? gapCount : null;
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
              aria-describedby={ok ? undefined : hintOf(m.value)}
              title={blockers[m.value]}
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
      {MODES.filter((m) => blockers[m.value]).map((m) => (
        <span key={m.value} id={hintOf(m.value)} className={styles.srOnly}>
          {blockers[m.value]}
        </span>
      ))}
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {backHref && (
        <Link href={backHref} className={styles.back}>
          回到處理這一頁
        </Link>
      )}
      {right && <div className={styles.right}>{right}</div>}
    </div>
  );
}
