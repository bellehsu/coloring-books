"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import styles from "./CharacterToggle.module.css";

export interface CharacterBody {
  include: boolean;
  redraw_background: boolean;
}

/** 「放主角」勾選框（一鍵處理頁與進階編輯共用）。取消勾選直接送；勾回去時先問怎麼處理背景。 */
export function CharacterToggle({
  page,
  backgroundUploaded,
  busy,
  onToggle,
}: {
  page: Page;
  /** 這頁的背景是使用者上傳的：不會重畫，只能保留背景。 */
  backgroundUploaded: boolean;
  busy: boolean;
  /** 呼叫端負責錯誤提示（不應 reject）。 */
  onToggle: (body: CharacterBody) => Promise<void>;
}) {
  const id = useId();
  const [asking, setAsking] = useState(false);
  const frame = page.placement?.frame != null;
  const switching = page.character_pending;
  // 後端只禁止相框頁「關掉」主角；沒放主角的相框頁可以加回來
  const frameLocked = frame && page.include_character;
  const disabled = busy || switching || frameLocked;
  // 有可用的角色動作：重畫背景只要 1 次（只畫背景），保留背景 0 次
  const hasPose = page.pose_generation_id != null;

  const send = async (body: CharacterBody) => {
    setAsking(false);
    await onToggle(body);
  };

  return (
    <div className={styles.box}>
      <label className={styles.label}>
        <input
          type="checkbox"
          checked={page.include_character}
          disabled={disabled}
          aria-describedby={`${id}-note`}
          onChange={(e) => {
            if (e.target.checked) setAsking(true);
            else void send({ include: false, redraw_background: true });
          }}
        />
        <span>放主角</span>
      </label>
      <p id={`${id}-note`} className={styles.note}>
        {frameLocked ? "相框頁要放主角" : switching ? "切換中…" : ""}
      </p>
      <Dialog open={asking} onClose={() => setAsking(false)} title="把主角加回來">
        <Choices>
          {!backgroundUploaded && (
            <ChoiceButton
              primary
              label="重畫背景（恐龍移到旁邊）"
              cost={hasPose ? 1 : 2}
              onClick={() => void send({ include: true, redraw_background: true })}
            />
          )}
          <ChoiceButton
            primary={backgroundUploaded}
            label="保留背景，主角我自己調位置"
            cost={hasPose ? 0 : 1}
            onClick={() => void send({ include: true, redraw_background: false })}
          />
          {backgroundUploaded && <p className={styles.note}>上傳的背景不會重畫。</p>}
        </Choices>
      </Dialog>
    </div>
  );
}

function ChoiceButton({ label, cost, primary, onClick }: { label: string; cost: number; primary?: boolean; onClick: () => void }) {
  return (
    <Button data-default={primary || undefined} variant={primary ? "primary" : "secondary"} className={styles.choice} onClick={onClick}>
      <span>{label}</span>
      <span className={styles.cost}>用 {cost} 次</span>
    </Button>
  );
}

function Choices({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  // Modal 開啟時把焦點給第一個可聚焦的元素（× 關閉）；預設選項在它之後搶回焦點
  useEffect(() => {
    queueMicrotask(() => ref.current?.querySelector<HTMLElement>("[data-default]")?.focus());
  }, []);
  return (
    <div ref={ref} className={styles.choices}>
      {children}
    </div>
  );
}
