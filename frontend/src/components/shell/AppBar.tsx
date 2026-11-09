import Link from "next/link";
import { Icons } from "@/components/ui/Icons";
import { QueuePill } from "./QueuePill";
import styles from "./AppBar.module.css";

export interface Crumb {
  label: string;
  href?: string;
}

export function AppBar({ crumbs = [] }: { crumbs?: Crumb[] }) {
  return (
    <header className={styles.bar}>
      <Link href="/" className={styles.logo} aria-label="繪本工作室">
        <span className={styles.mark}>
          <Icons.pen size={18} />
        </span>
        <b className={styles.name}>繪本工作室</b>
      </Link>
      {crumbs.length > 0 && (
        <nav aria-label="目前位置" className={styles.crumbs}>
          {crumbs.map((c, i) => (
            <span key={i} className={styles.crumb}>
              <Icons.chevron size={16} />
              {c.href ? (
                <Link href={c.href} className={styles.link}>
                  {c.label}
                </Link>
              ) : (
                <span aria-current="page" className={styles.here}>
                  {c.label}
                </span>
              )}
            </span>
          ))}
        </nav>
      )}
      <QueuePill />
    </header>
  );
}
