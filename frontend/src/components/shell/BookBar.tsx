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
  onExport?: () => void;
  onSettings?: () => void;
}

/** 進階編輯專用的標題列：書名、版面說明、回到「主角與故事」、匯出、設定。 */
export function BookBar({ book, onExport, onSettings }: BookBarProps) {
  return (
    <div className={styles.bar}>
      <div className={styles.titles}>
        <h1 className={styles.title}>{book.title}</h1>
        <span className={styles.sub}>
          {PAPER[book.paper_size]}・{PRINT[book.print_mode]}
        </span>
      </div>
      <Link href={`/books/${book.id}/setup`} className={styles.setupLink}>
        主角與故事
      </Link>
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
