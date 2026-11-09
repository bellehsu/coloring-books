import Link from "next/link";
import type { Book } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { Icons } from "@/components/ui/Icons";
import styles from "./BookBar.module.css";

const PAPER: Record<Book["paper_size"], string> = {
  a4: "A4",
  a5: "A5",
  b5: "B5",
  letter: "Letter",
  square_21: "21 cm 正方形",
};
const PRINT: Record<Book["print_mode"], string> = {
  home: "家用印表機",
  print_shop: "印刷廠（含 3 mm 出血）",
};

export interface BookBarProps {
  book: Book;
  current: "character" | "pages" | "export";
  onExport?: () => void;
  onSettings?: () => void;
}

export function BookBar({ book, current, onExport, onSettings }: BookBarProps) {
  const hasCharacter = book.character_generation_id != null;
  const charHref = `/books/${book.id}/character`;
  const pagesHref = `/books/${book.id}/editor`;
  const pagesLabel = hasCharacter ? `頁面 ${book.stats.passed} / ${book.stats.pages}` : "頁面";
  const hintId = `pages-hint-${book.id}`;

  return (
    <div className={styles.bar}>
      <div className={styles.titles}>
        <h1 className={styles.title}>{book.title}</h1>
        <span className={styles.sub}>
          {PAPER[book.paper_size]}・{PRINT[book.print_mode]}
        </span>
      </div>
      <ol aria-label="製作步驟" className={styles.steps}>
        <li>
          {current === "character" ? (
            <span aria-current="step" className={`${styles.pill} ${styles.now}`}>
              <span className={`${styles.badge} ${styles.badgeNow}`}>{hasCharacter ? <Icons.check size={14} /> : 1}</span>
              角色
            </span>
          ) : (
            <Link href={charHref} className={`${styles.pill} ${hasCharacter ? styles.done : styles.todo}`}>
              <span className={`${styles.badge} ${hasCharacter ? styles.badgeDone : styles.badgeTodo}`}>
                {hasCharacter ? <Icons.check size={14} /> : 1}
              </span>
              角色
            </Link>
          )}
        </li>
        <li aria-hidden="true" className={styles.sep} />
        <li>
          {!hasCharacter ? (
            <>
              <button
                type="button"
                aria-disabled="true"
                aria-describedby={hintId}
                title="請先設定角色"
                className={`${styles.pill} ${styles.todo} ${styles.disabled}`}
              >
                <span className={`${styles.badge} ${styles.badgeTodo}`}>2</span>
                頁面
              </button>
              <span id={hintId} className={styles.srOnly}>
                請先設定角色
              </span>
            </>
          ) : current === "pages" ? (
            <span aria-current="step" className={`${styles.pill} ${styles.now}`}>
              <span className={`${styles.badge} ${styles.badgeNow}`}>2</span>
              {pagesLabel}
            </span>
          ) : (
            <Link href={pagesHref} className={`${styles.pill} ${styles.todo}`}>
              <span className={`${styles.badge} ${styles.badgeTodo}`}>2</span>
              {pagesLabel}
            </Link>
          )}
        </li>
        <li aria-hidden="true" className={styles.sep} />
        <li>
          <button
            type="button"
            onClick={onExport}
            aria-current={current === "export" ? "step" : undefined}
            className={`${styles.pill} ${styles.todo}`}
          >
            <span className={`${styles.badge} ${styles.badgeTodo}`}>3</span>
            匯出
          </button>
        </li>
      </ol>
      <div className={styles.actions}>
        <IconButton label="繪本設定" onClick={onSettings}>
          <Icons.sliders />
        </IconButton>
        <Button variant="primary" icon={<Icons.download size={18} />} onClick={onExport}>
          匯出 PDF
        </Button>
      </div>
    </div>
  );
}
