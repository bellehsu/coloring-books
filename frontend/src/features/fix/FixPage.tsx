"use client";

import { useId, useState } from "react";
import Link from "next/link";
import type { Book, Generation, OverviewPage, Page } from "@/api/client";
import { isActive, useBook, useBookOverview, useGenerations, usePagesFollowJobs, useQA } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { errMsg } from "@/features/editing/errMsg";
import { recomposeRunning } from "@/lib/pageState";
import type { StrokeOp } from "@/lib/strokes";
import { useFixPages } from "./fixApi";
import { FixHeader } from "./FixHeader";
import { neighbours, pageState, penBlocked, showRestore, type FixFilter } from "./fixState";
import { FixPreview, type View } from "./FixPreview";
import { BackgroundRow } from "./rows/BackgroundRow";
import { CaptionRow } from "./rows/CaptionRow";
import { LinesRow } from "./rows/LinesRow";
import { PoseRow } from "./rows/PoseRow";
import { useFixActions } from "./useFixActions";
import { usePlacementSaves } from "./usePlacementSaves";
import styles from "./FixPage.module.css";

export interface FixPageProps {
  bookId: number;
  pageId: number;
  /** 從 B 的「請看一下」打開（?filter=attention）：上一頁／下一頁與「這頁可以印了」只在要看的頁之間跳。 */
  filter: FixFilter;
}

/**
 * C 處理一頁（spec §4.4、mockups/3_fix.png）：頁首（← 整本書、標題與狀態、↶ 復原／↷ 重做、上一頁／下一頁），
 * 預覽（要印的線稿／彩色原圖、圈出沒接起來的線、拖角色），每一項的修改列（角色動作、背景、線條、圖下方的字）
 * 與「↺ 還原成自動畫的那張」，失敗時的原因卡與「再試一次」，「這頁可以印了 ✓」。
 * 所有修改都走 ?oneclick=true：後端先存復原快照，需要時重新合成，跑完仍是可以印。
 */
export function FixPage({ bookId, pageId, filter }: FixPageProps) {
  const book = useBook(bookId);
  const overview = useBookOverview(bookId);
  const gens = useGenerations(bookId, { pageId });
  usePagesFollowJobs(bookId, gens.data);
  const ovPage = overview.data?.pages.find((p) => p.id === pageId);
  const genBusy = gens.data?.some(isActive) ?? false;
  const pages = useFixPages(bookId, pageId, ovPage?.state === "working" || genBusy);
  const page = pages.data?.find((p) => p.id === pageId);

  if (!page || !book.data) {
    const err = pages.error ?? book.error;
    const missing = pages.data && !page;
    return (
      <main className={styles.main}>
        <div className={styles.loadMsg}>
          {err || missing ? (
            <>
              <p role="alert">{missing ? "找不到這一頁" : errMsg(err, "無法載入這一頁")}</p>
              <Link href={`/books/${bookId}`}>回到整本書</Link>
            </>
          ) : (
            <p aria-busy="true">載入中…</p>
          )}
        </div>
      </main>
    );
  }
  return (
    <FixBody
      bookId={bookId}
      book={book.data}
      page={page}
      filter={filter}
      gens={gens.data ?? []}
      overviewPages={overview.data?.pages}
    />
  );
}

interface BodyProps {
  bookId: number;
  book: Book;
  page: Page;
  filter: FixFilter;
  gens: Generation[];
  overviewPages: OverviewPage[] | undefined;
}

function FixBody({ bookId, book, page, filter, gens, overviewPages }: BodyProps) {
  const pageId = page.id;
  const hasArt = page.selected_generation_id != null;
  const qaQ = useQA(pageId, hasArt);
  const qa = qaQ.data;
  // QA 載入失敗（例如原圖遺失 404）：畫筆與線條列說明原因，可以再試一次
  const qaError = !qa && qaQ.error ? errMsg(qaQ.error, "線稿載入失敗") : null;
  const retryQA = () => void qaQ.refetch();
  const ovPage = overviewPages?.find((p) => p.id === pageId);
  const state = pageState(page, ovPage);
  const { prev, next } = overviewPages ? neighbours(overviewPages, pageId, filter) : { prev: null, next: null };

  const placement = usePlacementSaves(bookId, pageId);
  const [view, setView] = useState<View>("print");
  const [drawTool, setDrawTool] = useState<StrokeOp | null>(null);
  const fix = useFixActions({ bookId, page, filter, next, placement, closePen: () => setDrawTool(null) });
  const { busy, textSaving } = fix;

  const blocked = penBlocked(page);
  const drawing = drawTool !== null && blocked === null && hasArt;
  const bgGen = gens.find((g) => g.id === page.background_generation_id);
  const poseGen = gens.find((g) => g.id === page.pose_generation_id);
  const bgActive = gens.some((g) => g.kind === "background" && isActive(g));
  const poseActive = gens.some((g) => g.kind === "pose" && isActive(g));
  // 合成失敗（recompose_pending＋attention）不算在做：顯示原因卡與「再試一次」
  const working = recomposeRunning(page) || state === "working";

  return (
    <main className={styles.main}>
      <FixHeader
        bookId={bookId}
        page={page}
        state={state}
        filter={filter}
        prev={prev}
        next={next}
        busy={busy}
        textSaving={textSaving}
        onUndo={() => void fix.undo()}
        onRedo={() => void fix.redo()}
      />

      <div className={styles.grid}>
        <FixPreview
          bookId={bookId}
          page={page}
          qa={qa}
          qaError={qaError}
          onRetryQA={retryQA}
          contentPx={book.layout.content_px}
          view={view}
          onViewChange={(v) => {
            setView(v);
            setDrawTool(null);
          }}
          drawTool={drawTool}
          drawing={drawing}
          onDrawDone={() => setDrawTool(null)}
          bgGen={bgGen}
          poseGen={poseGen}
          placement={placement}
          working={working}
        />

        <div className={styles.side}>
          {page.attention === "failed" && <ReasonCard detail={page.attention_detail} busy={busy} saving={textSaving} onRetry={() => void fix.retry()} />}
          {page.attention === "review_pose" && <p className={styles.rowWarn}>{page.attention_detail}</p>}
          <ul className={styles.items} aria-label="這一頁的東西">
            <PoseRow
              bookId={bookId}
              page={page}
              hasCharacter={book.character_generation_id != null}
              active={poseActive}
              flushEdits={fix.flushEdits}
              restore={showRestore(page, "pose") ? () => void fix.restore("pose") : null}
              busy={busy}
              textKey={fix.historyEpoch}
              trackSave={fix.trackSave}
            />
            <BackgroundRow
              bookId={bookId}
              page={page}
              bgGen={bgGen}
              active={bgActive}
              flushEdits={fix.flushEdits}
              restore={showRestore(page, "background") ? () => void fix.restore("background") : null}
              busy={busy}
              textKey={fix.historyEpoch}
              trackSave={fix.trackSave}
            />
            <LinesRow
              bookId={bookId}
              page={page}
              qa={qa}
              qaError={qaError}
              onRetryQA={retryQA}
              blocked={blocked}
              tool={drawing ? drawTool : null}
              onTool={(t) => {
                setView("print");
                setDrawTool(t);
              }}
              restore={showRestore(page, "lines") ? () => void fix.restore("lines") : null}
              busy={busy}
            />
            <CaptionRow
              bookId={bookId}
              page={page}
              textKey={fix.historyEpoch}
              busy={busy || page.recompose_pending}
              trackSave={fix.trackSave}
            />
          </ul>
          {hasArt && qa && qa.gap_count > 0 && (
            <p className={styles.note}>線沒接起來也可以印；想讓顏色不漏出去，再按「自動補線」或用畫筆接起來。</p>
          )}
          {hasArt && (
            <Button
              size="lg"
              className={styles.accept}
              disabled={busy || fix.acceptBlocked !== null}
              aria-busy={textSaving}
              onClick={() => void fix.accept()}
            >
              這頁可以印了 ✓
            </Button>
          )}
          {hasArt && fix.acceptBlocked && <p className={styles.rowWarn}>{fix.acceptBlocked}</p>}
          <Link href={`/books/${bookId}/editor?page=${pageId}`} className={styles.advanced}>
            進階編輯
          </Link>
        </div>
      </div>
    </main>
  );
}

function ReasonCard({
  detail,
  busy,
  saving,
  onRetry,
}: {
  detail: string | null;
  busy: boolean;
  saving: boolean;
  onRetry: () => void;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={styles.reason}>
      <h2 id={id} className={styles.reasonKicker}>
        為什麼請你看
      </h2>
      <p className={styles.reasonText}>{detail || "這一頁沒做好"}</p>
      <p className={styles.reasonHint}>按「再試一次」重新做這一頁；也可以換一張圖或上傳背景。</p>
      <Button variant="primary" disabled={busy} aria-busy={saving} onClick={onRetry}>
        再試一次
      </Button>
    </section>
  );
}
