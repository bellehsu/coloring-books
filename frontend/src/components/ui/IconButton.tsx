import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./IconButton.module.css";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> {
  label: string;
  /** 40 僅限工具列，其餘一律 44（可點擊目標）。 */
  size?: 40 | 44;
  active?: boolean;
  children: ReactNode;
}

export function IconButton({ label, size = 44, active, className, children, type = "button", ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      data-active={active ? "true" : undefined}
      className={[styles.btn, className].filter(Boolean).join(" ")}
      style={{ width: size, height: size }}
      {...rest}
    >
      {children}
    </button>
  );
}
