import type { UseQueryResult } from "@tanstack/react-query";
import type { Page, QA } from "@/api/client";
import { errMsg } from "@/features/editing/errMsg";
import { LineFixStage } from "@/features/editing/LineFixStage";
import stageStyles from "@/features/editing/stage.module.css";

/** ② 補線的舞台：修線畫布；線稿（QA）還在算或載入失敗時說明。 */
export function LineStageArea({
  bookId,
  page,
  qa,
  qaQ,
  contentPx,
  zoom,
  onZoomChange,
}: {
  bookId: number;
  page: Page;
  qa: QA | undefined;
  qaQ: UseQueryResult<QA>;
  contentPx: [number, number];
  zoom: number;
  onZoomChange: (zoom: number) => void;
}) {
  return qa ? (
    <LineFixStage
      bookId={bookId}
      pageId={page.id}
      genId={page.selected_generation_id!}
      qa={qa}
      contentPx={contentPx}
      lineWidthPx={page.line_width_px}
      zoom={zoom}
      onZoomChange={onZoomChange}
    />
  ) : qaQ.isError ? (
    <p role="alert" className={stageStyles.stageMsg}>
      {errMsg(qaQ.error, "無法載入線稿")}
    </p>
  ) : (
    <p className={stageStyles.stageMsg} aria-busy="true">
      線稿計算中…
    </p>
  );
}
