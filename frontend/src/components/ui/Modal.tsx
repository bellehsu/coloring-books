"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { Icons } from "./Icons";
import styles from "./Modal.module.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

const modalStack: object[] = [];
let lockCount = 0;
let prevOverflow = "";
function lockScroll() {
  if (lockCount++ === 0) {
    prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
}
function unlockScroll() {
  if (--lockCount === 0) document.body.style.overflow = prevOverflow;
}

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}

/**
 * Dialog 與 Drawer 共用：role=dialog、Esc／點背景關閉、焦點進入第一個可聚焦元素、
 * Tab 循環、關閉後焦點回到開啟前的元素。
 */
export function Modal({ open, onClose, title, children, variant }: ModalProps & { variant: "dialog" | "drawer" }) {
  const panel = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  const titleId = useId();
  const downOnBackdrop = useRef(false);

  useEffect(() => {
    if (!open) return;
    const token = {};
    modalStack.push(token);
    lockScroll();
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();

    // 只有最上層的 modal 回應；焦點落在 body 時仍有效。
    const onDocKey = (e: globalThis.KeyboardEvent) => {
      if (modalStack[modalStack.length - 1] !== token) return;
      const el = panel.current;
      if (!el) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const inside = el.contains(active);
      if (e.shiftKey && (active === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onDocKey);
    return () => {
      document.removeEventListener("keydown", onDocKey);
      modalStack.splice(modalStack.indexOf(token), 1);
      unlockScroll();
      if (opener && opener.isConnected) opener.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      data-testid="overlay"
      className={`${styles.overlay} ${styles[variant]}`}
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        const ok = downOnBackdrop.current && e.target === e.currentTarget;
        downOnBackdrop.current = false;
        if (ok) onClose();
      }}
    >
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`${styles.panel} ${styles[`${variant}Panel`]}`}
      >
        <div className={styles.head}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          <button type="button" aria-label="關閉" className={styles.close} onClick={onClose}>
            <Icons.close size={18} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
