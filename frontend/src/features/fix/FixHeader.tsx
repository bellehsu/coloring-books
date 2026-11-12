import Link from "next/link";
import type { OverviewPage, Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { STATE_LABEL, type PageState } from "@/features/book/overviewState";
import { fixHref, pageTitle, type FixFilter } from "./fixState";
import styles from "./FixPage.module.css";

/** C 的頁首：← 整本書、標題與狀態、↶ 復原／↷ 重做、上一頁／下一頁。 */
export function FixHeader({
  bookId,
  page,
  state,
  filter,
  prev,
  next,
  busy,
  textSaving,
  onUndo,
  onRedo,
}: {
  bookId: number;
  page: Page;
  state: PageState;
  filter: FixFilter;
  prev: OverviewPage | null;
  next: OverviewPage | null;
  busy: boolean;
  textSaving: boolean;
  onUndo: () => void;
  onRedo: () => void;
}) {
  return (
    <header className={styles.head}>
      <Link href={`/books/${bookId}`} className={styles.back}>
        <span aria-hidden="true">←</span> 整本書
      </Link>
      <h1 className={styles.h1}>{pageTitle(page)}</h1>
      <span className={`${styles.pill} ${styles[`pill_${state}`]}`}>{STATE_LABEL[state]}</span>
      <div className={styles.headActions}>
        <Button className={styles.headBtn} disabled={!page.can_undo || busy} aria-busy={textSaving} onClick={onUndo}>
          <span aria-hidden="true">↶</span> 復原
        </Button>
        <Button className={styles.headBtn} disabled={!page.can_redo || busy} aria-busy={textSaving} onClick={onRedo}>
          <span aria-hidden="true">↷</span> 重做
        </Button>
        <nav aria-label="換頁" className={styles.pager}>
          <PagerLink href={prev ? fixHref(bookId, prev.id, filter) : null} label="上一頁" arrow="‹" before />
          <PagerLink href={next ? fixHref(bookId, next.id, filter) : null} label="下一頁" arrow="›" />
        </nav>
      </div>
    </header>
  );
}

function PagerLink({ href, label, arrow, before }: { href: string | null; label: string; arrow: string; before?: boolean }) {
  const text = <span className={styles.pagerText}>{label}</span>;
  const content = before ? (
    <>
      <span aria-hidden="true">{arrow}</span> {text}
    </>
  ) : (
    <>
      {text} <span aria-hidden="true">{arrow}</span>
    </>
  );
  if (!href)
    return (
      <span className={`${styles.pagerLink} ${styles.pagerOff}`} aria-disabled="true">
        {content}
      </span>
    );
  return (
    <Link href={href} className={styles.pagerLink}>
      {content}
    </Link>
  );
}
