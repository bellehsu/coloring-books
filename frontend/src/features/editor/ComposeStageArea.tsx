import { useSyncExternalStore } from "react";
import { useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { DEFAULT_PLACEMENT, type Generation, type Page, type Placement } from "@/api/client";
import { ComposePreview, ComposeStage, ComposeStatus, type AutoComposeStatus } from "@/features/editing/ComposeStage";
import { errMsg } from "@/features/editing/errMsg";
import { placementSaverFor } from "@/features/editing/placementSaver";
import stageStyles from "@/features/editing/stage.module.css";

const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

export interface ComposeStageAreaProps {
  page: Page;
  /** 這頁的生成紀錄（所有種類，含捨棄的）。 */
  gensQ: UseQueryResult<Generation[]>;
  /** 頁面選用的背景與角色動作（找不到或還沒選時 undefined）。 */
  backgroundGen: Generation | undefined;
  poseGen: Generation | undefined;
  /** 紙張的生成尺寸（只有角色動作時用它的比例）。 */
  generationSize: readonly [number, number];
  composeStatus: AutoComposeStatus;
  /** 閒置時，選定圖就是目前這組背景＋角色動作＋位置的合成圖。 */
  upToDate: boolean;
  /** 放開拖曳／縮放、按鍵、翻轉、重設位置。 */
  onPlace: (p: Placement) => void;
  onRetryCompose: () => void;
  onRetryPlacement: () => void;
}

/**
 * ① 構圖的舞台：兩個都選了才能擺位置（ComposeStage＋自動合成狀態）；只有一個時有什麼就顯示什麼；
 * 圖片缺漏（沒有圖、沒有遮罩 → 沒有 cutout_url）由 ComposeStage 說明要重新產生。
 * 角色位置：還沒存好時畫面用最後存的值（placementSaver），否則用伺服器的。
 */
export function ComposeStageArea({
  page,
  gensQ,
  backgroundGen,
  poseGen,
  generationSize,
  composeStatus,
  upToDate,
  onPlace,
  onRetryCompose,
  onRetryPlacement,
}: ComposeStageAreaProps) {
  const qc = useQueryClient();
  const placementSaver = placementSaverFor(qc);
  const savedPlacement = useSyncExternalStore(
    placementSaver.subscribe,
    () => placementSaver.latest(page.id),
    () => undefined,
  );
  const placementFailed = useSyncExternalStore(
    placementSaver.subscribe,
    () => placementSaver.failed(page.id),
    () => false,
  );
  const placement: Placement = savedPlacement ?? page.placement ?? DEFAULT_PLACEMENT;
  const gens = gensQ.data ?? [];
  const hasBg = page.background_generation_id != null;
  const hasPose = page.pose_generation_id != null;
  const busyKind = (kind: Generation["kind"]) => gens.some((g) => g.kind === kind && isActive(g));

  if (!hasBg && !hasPose) {
    return (
      <p className={stageStyles.stageMsg}>
        {busyKind("background") || busyKind("pose") ? "背景與角色動作產生中…" : "先選好背景和角色動作"}
      </p>
    );
  }
  if (!hasBg || !hasPose) {
    // 只有一個：有什麼就顯示什麼，另一個產生中或還沒選
    return (
      <ComposePreview
        backgroundUrl={backgroundGen?.image_url}
        cutoutUrl={hasPose ? poseGen?.cutout_url : null}
        aspect={generationSize[0] / generationSize[1]}
        message={
          !hasBg
            ? busyKind("background") ? "背景產生中…" : "先選好背景"
            : busyKind("pose") ? "角色動作產生中…" : "先選好角色動作"
        }
      />
    );
  }
  if (backgroundGen && poseGen) {
    return (
      <ComposeStage
        backgroundUrl={backgroundGen.image_url}
        cutoutUrl={poseGen.cutout_url}
        placement={placement}
        onChange={(p) => onPlace(p)}
        status={
          <ComposeStatus
            status={composeStatus}
            upToDate={upToDate}
            onRetry={onRetryCompose}
            placementFailed={placementFailed}
            onRetryPlacement={onRetryPlacement}
          />
        }
      />
    );
  }
  if (gensQ.data && !gensQ.isFetching) {
    return (
      <p role="alert" className={stageStyles.stageMsg}>
        找不到選用的背景或角色動作，請重新選一張
      </p>
    );
  }
  return gensQ.isError ? (
    <p role="alert" className={stageStyles.stageMsg}>
      {errMsg(gensQ.error, "無法載入圖片")}
    </p>
  ) : (
    <p className={stageStyles.stageMsg} aria-busy="true">
      載入中…
    </p>
  );
}
