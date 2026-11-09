"use client";

import { useId, useRef, useState } from "react";
import type { Page } from "@/api/client";
import { Segmented } from "@/components/ui/Segmented";
import styles from "./Inspector.module.css";

type Detail = Page["line_detail"];
type LinePatch = { line_detail?: Detail; line_width_px?: number };

export const WIDTH_MIN = 8;
export const WIDTH_MAX = 16;

/** 300 DPI 的像素換成 mm（1 位小數）。 */
export const pxToMm = (px: number) => ((px / 300) * 25.4).toFixed(1);

const DETAILS: { value: Detail; label: string }[] = [
  { value: "simple", label: "簡單" },
  { value: "standard", label: "標準" },
  { value: "detailed", label: "細緻" },
];

export interface LinePanelProps {
  /** 呼叫端請用 key={page.id}。 */
  page: Page;
  /** PATCH 本頁；失敗時呼叫端顯示 toast 並 reject。 */
  onPatch: (body: LinePatch) => Promise<unknown>;
}

/** 修線模式的線條設定：細緻度（立即存）、線寬 8–16 px（放開時存）。線稿即時重算，不重新生成。 */
export function LinePanel({ page, onPatch }: LinePanelProps) {
  const id = useId();
  const [detail, setDetail] = useState<Detail>(page.line_detail);
  const [width, setWidth] = useState(page.line_width_px);
  const sentWidth = useRef<number>(page.line_width_px);

  function changeDetail(d: Detail) {
    const prev = detail;
    setDetail(d);
    onPatch({ line_detail: d }).catch(() => setDetail(prev));
  }

  function commitWidth() {
    if (width === sentWidth.current) return;
    const prev = sentWidth.current;
    sentWidth.current = width;
    onPatch({ line_width_px: width }).catch(() => {
      sentWidth.current = prev;
      setWidth(prev);
    });
  }

  return (
    <section aria-labelledby={`${id}-h`} className={styles.panel}>
      <h2 id={`${id}-h`} className={styles.h2}>
        線條（即時重算，不重新生成）
      </h2>
      <Segmented kind="radio" label="細緻度" value={detail} onChange={changeDetail} options={DETAILS} />
      <div className={styles.field}>
        <label htmlFor={`${id}-w`} className={styles.rangeLabel}>
          線寬{" "}
          <span className={styles.value}>
            {width} px・約 {pxToMm(width)} mm
          </span>
        </label>
        <input
          id={`${id}-w`}
          type="range"
          min={WIDTH_MIN}
          max={WIDTH_MAX}
          step={1}
          value={width}
          className={styles.range}
          onChange={(e) => setWidth(Number(e.target.value))}
          onPointerUp={commitWidth}
          onKeyUp={commitWidth}
          onBlur={commitWidth}
        />
      </div>
      <p className={styles.note2}>修線存成獨立圖層，調整細緻度或線寬不會讓修改消失。</p>
    </section>
  );
}
