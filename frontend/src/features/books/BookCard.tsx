import Link from "next/link";
import type { Book } from "@/api/client";
import { Chip } from "@/components/ui/Chip";
import { nextStep } from "@/lib/nextStep";
import { PAPER_LABELS, PRINT_MODE_LABELS } from "@/lib/layout";
import { BookCardMenu } from "./BookCardMenu";
import styles from "./BookCard.module.css";

export function BookCard({ book }: { book: Book }) {
  // 進度＝B 的「可以印」頁數（請看一下的頁不算做好）
  const { pages, ready } = book.stats;
  const step = nextStep(book);
  // 每本書都從 B 整本書進去（還沒開始的書在 B 會引導到「主角與故事」）
  const href = `/books/${book.id}`;
  const [cw, ch] = book.layout.content_px;
  const pct = pages ? Math.round((ready / pages) * 100) : 0;
  return (
    <div className={styles.card}>
      <BookCardMenu book={book} />
      <Link href={href} className={styles.link}>
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
          <b className={styles.title}>
            {book.title}
            {book.shared_at && <span className={styles.badge}>已分享</span>}
          </b>
          <span className={styles.meta}>
            {PAPER_LABELS[book.paper_size]}・{PRINT_MODE_LABELS[book.print_mode]}
          </span>
        </div>
        <div className={styles.progress}>
          <div className={styles.track} aria-hidden="true">
            <div className={styles.fill} style={{ width: `${pct}%` }} />
          </div>
          <span className={styles.count}>{pages ? `${ready} / ${pages} 頁可以印` : "尚無頁面"}</span>
        </div>
        <Chip tone={step.tone}>{step.text}</Chip>
      </div>
      </Link>
    </div>
  );
}
