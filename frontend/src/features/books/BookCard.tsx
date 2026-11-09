import Link from "next/link";
import type { Book } from "@/api/client";
import { Chip } from "@/components/ui/Chip";
import { nextStep } from "@/lib/nextStep";
import { PAPER_LABELS, PRINT_MODE_LABELS } from "@/lib/layout";
import styles from "./BookCard.module.css";

export function BookCard({ book }: { book: Book }) {
  const { pages, passed } = book.stats;
  const step = nextStep(book);
  const href = book.character_generation_id == null ? `/books/${book.id}/character` : `/books/${book.id}/editor`;
  const [cw, ch] = book.layout.content_px;
  const pct = pages ? Math.round((passed / pages) * 100) : 0;
  return (
    <Link href={href} className={styles.card}>
      <div className={styles.stage}>
        <div className={styles.paper} style={{ aspectRatio: `${cw} / ${ch}` }}>
          {book.cover_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={book.cover_url} alt="" className={styles.cover} />
          )}
        </div>
      </div>
      <div className={styles.body}>
        <div className={styles.titles}>
          <b className={styles.title}>{book.title}</b>
          <span className={styles.meta}>
            {PAPER_LABELS[book.paper_size]}・{PRINT_MODE_LABELS[book.print_mode]}
          </span>
        </div>
        <div className={styles.progress}>
          <div className={styles.track} aria-hidden="true">
            <div className={styles.fill} style={{ width: `${pct}%` }} />
          </div>
          <span className={styles.count}>{pages ? `${passed} / ${pages} 頁通過` : "尚無頁面"}</span>
        </div>
        <Chip tone={step.tone}>{step.text}</Chip>
      </div>
    </Link>
  );
}
