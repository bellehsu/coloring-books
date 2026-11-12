"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Dialog } from "@/components/ui/Dialog";
import { Icons } from "@/components/ui/Icons";
import { STATE_LABEL } from "@/features/book/overviewState";
import styles from "./Filmstrip.module.css";

export interface FilmstripProps {
  pages: Page[];
  currentId: number | null;
  /** 有 queued／running 候選的頁。 */
  busyPageIds: ReadonlySet<number>;
  /** 縮圖紙張比例（book.layout.content_px）。 */
  paperAspect: readonly [number, number];
  onSelect: (pageId: number) => void;
  onAdd: () => void;
  adding?: boolean;
  /** dir：-1 上移、1 下移。 */
  onMove: (pageId: number, dir: -1 | 1) => void;
  /** 使用者在確認對話框按下「刪除」後才呼叫。 */
  onDelete: (pageId: number) => void;
  /** 手機寬度（§4.5）：清單改成頂端「第 N 頁 / M」下拉選單，旁邊是這一頁的狀態、更多動作與新增。 */
  compact?: boolean;
}

type Tone = "ok" | "warn" | "draft" | "busy";

/** 頁面的狀態標籤；C 的狀態（製作中、請看一下）用 B 的字，優先順序也和 B 一樣（製作中＞請看一下＞完成）。 */
export function pageStatus(p: Page, busy: boolean): { tone: Tone; label: string } {
  if (busy) return { tone: "busy", label: "生成中" };
  if (p.recompose_pending) return { tone: "busy", label: STATE_LABEL.working };
  if (p.attention) return { tone: "warn", label: STATE_LABEL.attention };
  if (p.status === "passed") return { tone: "ok", label: "完成" };
  if (p.needs_fix) return { tone: "warn", label: "需要修線" };
  return { tone: "draft", label: "草稿" };
}

export function Filmstrip({
  pages,
  currentId,
  busyPageIds,
  paperAspect,
  onSelect,
  onAdd,
  adding,
  onMove,
  onDelete,
  compact = false,
}: FilmstripProps) {
  const [confirm, setConfirm] = useState<{ id: number; n: number } | null>(null);
  const passed = pages.filter((p) => p.status === "passed").length;
  const total = pages.length;
  const addButton = (
    <button type="button" aria-label="新增頁面" title="新增頁面" className={styles.add} disabled={adding} onClick={onAdd}>
      <Icons.plus size={18} />
    </button>
  );
  const ci = pages.findIndex((p) => p.id === currentId);
  const cur = ci >= 0 ? pages[ci] : undefined;
  const curStatus = cur ? pageStatus(cur, busyPageIds.has(cur.id)) : null;

  return (
    <aside aria-label="頁面" className={styles.film} data-compact={compact}>
      {compact ? (
        <div className={styles.head}>
          <span className={styles.selectWrap}>
            <select
              aria-label="選擇頁面"
              className={styles.select}
              value={currentId ?? ""}
              onChange={(e) => onSelect(Number(e.target.value))}
            >
              {pages.map((p, i) => (
                <option key={p.id} value={p.id}>{`第 ${i + 1} 頁 / ${total}`}</option>
              ))}
            </select>
            <Icons.chevron size={16} className={styles.selectChev} />
          </span>
          {curStatus && (
            <span className={styles.curStatus}>
              <Chip tone={curStatus.tone}>{curStatus.label}</Chip>
            </span>
          )}
          <span className={styles.tools}>
            {cur && (
              <PageMenu
                n={ci + 1}
                canUp={ci > 0}
                canDown={ci < total - 1}
                onMove={(dir) => onMove(cur.id, dir)}
                onDelete={() => setConfirm({ id: cur.id, n: ci + 1 })}
              />
            )}
            {addButton}
          </span>
        </div>
      ) : (
        <div className={styles.head}>
          <b className={styles.h}>頁面</b>
          <span className={styles.count}>{total} 頁</span>
          {addButton}
        </div>
      )}
      <div
        role="progressbar"
        aria-label="整本進度"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={passed}
        aria-valuetext={`${passed} / ${total} 頁完成`}
        className={styles.bar}
      >
        <div className={styles.fill} style={{ width: total ? `${(passed / total) * 100}%` : 0 }} />
      </div>
      {!compact && (
        <ol className={styles.list}>
          {pages.map((p, i) => {
            const n = i + 1;
            const on = p.id === currentId;
            const st = pageStatus(p, busyPageIds.has(p.id));
            return (
              <li key={p.id} className={styles.item} data-on={on ? "true" : "false"}>
                <button
                  type="button"
                  className={styles.pick}
                  aria-current={on ? "true" : undefined}
                  onClick={() => onSelect(p.id)}
                >
                  <span className={styles.thumb} style={{ aspectRatio: `${paperAspect[0]} / ${paperAspect[1]}` }}>
                    {p.lineart_url && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={p.lineart_url} alt="" className={styles.img} loading="lazy" />
                    )}
                  </span>
                  <span className={styles.meta}>
                    <span className={styles.name}>第 {n} 頁</span>
                    <span className={styles.scene}>{p.scene_prompt || "（還沒有場景描述）"}</span>
                    <Chip tone={st.tone}>{st.label}</Chip>
                  </span>
                </button>
                <PageMenu
                  n={n}
                  canUp={i > 0}
                  canDown={i < total - 1}
                  onMove={(dir) => onMove(p.id, dir)}
                  onDelete={() => setConfirm({ id: p.id, n })}
                />
              </li>
            );
          })}
        </ol>
      )}

      <Dialog open={confirm !== null} onClose={() => setConfirm(null)} title={confirm ? `刪除第 ${confirm.n} 頁？` : ""}>
        <p className={styles.confirmText}>這一頁的背景、角色動作、所有候選圖與修線都會一起刪除，無法復原。</p>
        <div className={styles.confirmActions}>
          <Button onClick={() => setConfirm(null)}>取消</Button>
          <Button
            variant="danger-outline"
            onClick={() => {
              const id = confirm!.id;
              setConfirm(null);
              onDelete(id);
            }}
          >
            刪除
          </Button>
        </div>
      </Dialog>
    </aside>
  );
}

function MoreIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  );
}

/** 「⋯」選單（menu button 模式）：開啟時聚焦第一個可用項目，↑↓／Home／End 移動，Esc 關閉並回到按鈕。 */
function PageMenu({
  n,
  canUp,
  canDown,
  onMove,
  onDelete,
}: {
  n: number;
  canUp: boolean;
  canDown: boolean;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  /** 選單以 fixed 定位（窄螢幕的橫向捲動清單會裁掉 absolute 的彈出層）。 */
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const menuId = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const enabledItems = () =>
    Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? []);

  useEffect(() => {
    if (!open) return;
    enabledItems()[0]?.focus();
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    // 捲動或縮放視窗後位置就不對了：直接關閉（選單本身的捲動不算）
    const onMove = (e: Event) => {
      if (!menu.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    const r = trigger.current?.getBoundingClientRect();
    if (r) {
      const w = 160;
      setPos({ top: r.bottom + 4, left: Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) });
    }
    setOpen(true);
  }

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  function onKeyDown(e: KeyboardEvent) {
    const items = enabledItems();
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    let j = -1;
    if (e.key === "ArrowDown") j = (i + 1) % items.length;
    else if (e.key === "ArrowUp") j = (i - 1 + items.length) % items.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = items.length - 1;
    else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close(true);
      return;
    } else if (e.key === "Tab") {
      setOpen(false);
      return;
    }
    if (j < 0 || items.length === 0) return;
    e.preventDefault();
    items[j].focus();
  }

  const act = (fn: () => void) => {
    close(true);
    fn();
  };

  return (
    <div ref={wrap} className={styles.menuWrap}>
      <button
        ref={trigger}
        type="button"
        className={styles.more}
        aria-label={`第 ${n} 頁的更多動作`}
        title="更多動作"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={toggle}
      >
        <MoreIcon />
      </button>
      {open && (
        <div
          ref={menu}
          id={menuId}
          role="menu"
          aria-label={`第 ${n} 頁`}
          className={styles.menu}
          style={pos ?? undefined}
          onKeyDown={onKeyDown}
        >
          <button type="button" role="menuitem" tabIndex={-1} disabled={!canUp} onClick={() => act(() => onMove(-1))}>
            上移
          </button>
          <button type="button" role="menuitem" tabIndex={-1} disabled={!canDown} onClick={() => act(() => onMove(1))}>
            下移
          </button>
          <button type="button" role="menuitem" tabIndex={-1} className={styles.danger} onClick={() => act(onDelete)}>
            刪除…
          </button>
        </div>
      )}
    </div>
  );
}
