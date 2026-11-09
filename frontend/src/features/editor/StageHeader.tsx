"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import styles from "./StageHeader.module.css";

export type EditorMode = "background" | "pose" | "compose" | "line" | "qa";

export const MODES: { value: EditorMode; label: string }[] = [
  { value: "background", label: "背景" },
  { value: "pose", label: "角色" },
  { value: "compose", label: "合成" },
  { value: "line", label: "線稿與修線" },
  { value: "qa", label: "QA 檢查" },
];

export const DEFAULT_MODE: EditorMode = "background";

export const isEditorMode = (v: string | null): v is EditorMode => MODES.some((m) => m.value === v);

/** 決定分頁能不能用的頁面狀態。 */
export interface ModeGate {
  hasCharacter: boolean;
  backgroundId: number | null;
  poseId: number | null;
  selectedId: number | null;
}

/**
 * 停用的分頁 → 原因（顯示在 tooltip 與 aria-describedby）；不在表內＝可用。
 * 背景永遠可用；角色要本書已設定角色；合成要已選背景＋角色動作；線稿與 QA 要有選定圖（舊流程頁面也成立）。
 */
export function modeBlockers(g: ModeGate): Partial<Record<EditorMode, string>> {
  const out: Partial<Record<EditorMode, string>> = {};
  if (!g.hasCharacter) out.pose = "請先在角色工作室設定角色";
  if (g.backgroundId == null && g.poseId == null) out.compose = "請先選好背景與角色動作";
  else if (g.backgroundId == null) out.compose = "請先選好背景";
  else if (g.poseId == null) out.compose = "請先選好角色動作";
  if (g.selectedId == null) out.line = out.qa = "請先確認合成";
  return out;
}

/** 目標模式能用就用它，否則回到背景。 */
export const allowedMode = (m: EditorMode, g: ModeGate): EditorMode => (modeBlockers(g)[m] ? DEFAULT_MODE : m);

export const tabIdOf = (prefix: string, m: EditorMode) => `${prefix}-tab-${m}`;

export interface StageHeaderProps {
  mode: EditorMode;
  onModeChange: (m: EditorMode) => void;
  /** 停用的分頁 → 原因（modeBlockers）。 */
  blockers: Partial<Record<EditorMode, string>>;
  /** QA 分頁的紅色數字（> 0 才顯示）。 */
  gapCount?: number;
  /** tab id 前綴（tabIdOf）與舞台 tabpanel 的 id。 */
  idPrefix: string;
  panelId: string;
  /** 右側插槽：修線模式放 <ZoomControls>。 */
  right?: ReactNode;
}

export function StageHeader({ mode, onModeChange, blockers, gapCount = 0, idPrefix, panelId, right }: StageHeaderProps) {
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
          const badge = m.value === "qa" && ok && gapCount > 0 ? gapCount : null;
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
      {right && <div className={styles.right}>{right}</div>}
    </div>
  );
}
