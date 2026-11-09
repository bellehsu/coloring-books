"use client";

import { useEffect, useId, useRef, type ReactNode, type KeyboardEvent } from "react";
import { Icons } from "./Icons";
import styles from "./Modal.module.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

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

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    return () => {
      if (opener && opener.isConnected) opener.focus();
    };
  }, [open]);

  if (!open) return null;

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onCloseRef.current();
      return;
    }
    if (e.key !== "Tab" || !panel.current) return;
    const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !panel.current.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !panel.current.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      data-testid="overlay"
      className={`${styles.overlay} ${styles[variant]}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={onKeyDown}
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
