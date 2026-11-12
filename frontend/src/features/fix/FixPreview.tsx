import { useState, type CSSProperties } from "react";
import { DEFAULT_PLACEMENT, type Generation, type Page, type Placement, type QA } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { ComposeStage } from "@/features/editing/ComposeStage";
import { LineFixStage } from "@/features/editing/LineFixStage";
import type { StrokeOp } from "@/lib/strokes";
import { printUrl } from "./fixState";
import type { PlacementSaves } from "./usePlacementSaves";
import styles from "./FixPage.module.css";

const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;

export type View = "print" | "color";

export interface FixPreviewProps {
  bookId: number;
  page: Page;
  qa: QA | undefined;
  qaError: string | null;
  onRetryQA: () => void;
  contentPx: [number, number];
  /** 要印的線稿／彩色原圖（父層管：按畫筆會切回要印的線稿）。 */
  view: View;
  onViewChange: (v: View) => void;
  /** 畫筆／擦除開著（null＝沒開）；drawing：真的能畫（有圖、沒被擋）。 */
  drawTool: StrokeOp | null;
  drawing: boolean;
  onDrawDone: () => void;
  bgGen: Generation | undefined;
  poseGen: Generation | undefined;
  placement: PlacementSaves;
  /** 這頁在重新做（重新合成、製作中）。 */
  working: boolean;
}

/** 預覽卡：要印的線稿（可圈出沒接起來的線）／彩色原圖（拖角色），畫筆開著時是修線畫布。 */
export function FixPreview({
  bookId,
  page,
  qa,
  qaError,
  onRetryQA,
  contentPx,
  view,
  onViewChange,
  drawTool,
  drawing,
  onDrawDone,
  bgGen,
  poseGen,
  placement,
  working,
}: FixPreviewProps) {
  const [showGaps, setShowGaps] = useState(false);
  const hasArt = page.selected_generation_id != null;
  return (
    <section aria-label="預覽" className={styles.previewCard}>
      <div className={styles.previewBar}>
        <Segmented<View>
          kind="radio"
          label="預覽"
          value={view}
          onChange={onViewChange}
          options={[
            { value: "print", label: "要印的線稿" },
            { value: "color", label: "彩色原圖" },
          ]}
        />
        {view === "print" && hasArt && !drawing && qa && (
          <label className={styles.gapToggle}>
            <input
              type="checkbox"
              checked={showGaps}
              disabled={qa.gap_count === 0}
              onChange={(e) => setShowGaps(e.target.checked)}
            />
            圈出沒接起來的線（{qa.gap_count} 處）
          </label>
        )}
      </div>
      <div className={styles.stage}>
        {drawing && page.selected_generation_id != null ? (
          <DrawStage
            bookId={bookId}
            page={page}
            genId={page.selected_generation_id}
            qa={qa}
            qaError={qaError}
            onRetryQA={onRetryQA}
            contentPx={contentPx}
            tool={drawTool}
            onDone={onDrawDone}
          />
        ) : view === "print" ? (
          <PrintPreview page={page} qa={qa} showGaps={showGaps} contentPx={contentPx} working={working} />
        ) : (
          <ColorPreview page={page} bgGen={bgGen} poseGen={poseGen} draft={placement.draft} onChange={placement.save} />
        )}
      </div>
      {/* 常駐的 live region：文字是之後放進去的，螢幕閱讀器才會唸 */}
      <p role="status" className={styles.working}>
        {working && "正在重新做這一頁…好了會自動更新"}
      </p>
    </section>
  );
}

function PrintPreview({
  page,
  qa,
  showGaps,
  contentPx,
  working,
}: {
  page: Page;
  qa: QA | undefined;
  showGaps: boolean;
  contentPx: readonly [number, number];
  working: boolean;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  if (page.selected_generation_id == null || !page.lineart_url) {
    return (
      <p className={styles.empty}>
        {working ? "製作中…好了會自動出現" : "這一頁還沒有圖。可以用右邊的按鈕畫一張，或回到整本書按「繼續做」。"}
      </p>
    );
  }
  if (showGaps && qa) {
    const [w, h] = contentPx;
    return (
      <div className={styles.paper} style={{ "--ar": w / h } as CSSProperties}>
        {/* eslint-disable-next-line @next/next/no-img-element -- 後端動態 PNG */}
        <img src={page.lineart_url} alt="線稿，橘色圈標出沒接起來的線" className={styles.paperImg} draggable={false} />
        {qa.gaps.map(([x, y], i) => (
          <span
            key={`${i}-${x}-${y}`}
            aria-hidden="true"
            data-testid="gap-marker"
            className={styles.gap}
            style={{ left: pct(x), top: pct(y) }}
          />
        ))}
      </div>
    );
  }
  const src = printUrl(page);
  if (failed === src) return <p className={styles.empty}>列印頁預覽載入失敗，請重新整理</p>;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- 後端動態 PNG（帶 ?v=）
    <img src={src} alt="要印的線稿" className={styles.printImg} draggable={false} onError={() => setFailed(src)} />
  );
}

function ColorPreview({
  page,
  bgGen,
  poseGen,
  draft,
  onChange,
}: {
  page: Page;
  bgGen: Generation | undefined;
  poseGen: Generation | undefined;
  draft: Placement | null;
  onChange: (p: Placement, via?: "key") => void;
}) {
  if (page.background_generation_id != null && page.pose_generation_id != null) {
    const bgUrl = bgGen?.image_url ?? `/api/generations/${page.background_generation_id}/image`;
    const cutUrl = poseGen ? poseGen.cutout_url : `/api/generations/${page.pose_generation_id}/cutout`;
    return (
      <div className={styles.colorWrap}>
        <ComposeStage
          backgroundUrl={bgUrl}
          cutoutUrl={cutUrl}
          placement={draft ?? (page.placement as Placement | null) ?? DEFAULT_PLACEMENT}
          onChange={onChange}
        />
      </div>
    );
  }
  if (page.selected_generation_id != null)
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={`/api/generations/${page.selected_generation_id}/image`} alt="彩色原圖" className={styles.printImg} />
    );
  return <p className={styles.empty}>這一頁還沒有圖。</p>;
}

function DrawStage({
  bookId,
  page,
  genId,
  qa,
  qaError,
  onRetryQA,
  contentPx,
  tool,
  onDone,
}: {
  bookId: number;
  page: Page;
  genId: number;
  qa: QA | undefined;
  qaError: string | null;
  onRetryQA: () => void;
  contentPx: [number, number];
  tool: StrokeOp | null;
  onDone: () => void;
}) {
  return (
    <div className={styles.drawWrap}>
      <div className={styles.drawBar}>
        <p className={styles.drawHint}>用「補線」把線接起來，「擦除」擦掉多的線。畫好會自動存。</p>
        <Button variant="sky" onClick={onDone}>
          畫好了
        </Button>
      </div>
      {qa ? (
        <LineFixStage
          key={`${genId}-${tool}`}
          bookId={bookId}
          pageId={page.id}
          genId={genId}
          qa={qa}
          contentPx={contentPx}
          lineWidthPx={page.line_width_px}
          oneclick
          initialTool={tool ?? "draw"}
          hideUndo
        />
      ) : qaError ? (
        <QAError message={qaError} onRetry={onRetryQA} />
      ) : (
        <p className={styles.empty} aria-busy="true">
          載入線稿中…
        </p>
      )}
    </div>
  );
}

/** 線稿 QA 載入失敗：後端的原因＋「再試一次」（重抓 QA）。 */
export function QAError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className={styles.qaError}>
      <p role="alert">{message}</p>
      <Button onClick={onRetry}>再試一次</Button>
    </div>
  );
}
