import type { Generation, Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { ComposeWorkspace } from "./ComposeWorkspace";
import type { ThumbKind } from "./Thumbs";
import styles from "./EditorPage.module.css";

/** ① 構圖的右欄：背景與角色動作卡片（ComposeWorkspace）＋「下一步：補線 →」（不能補線時說明原因）。 */
export function ComposePanel({
  bookId,
  page,
  background,
  pose,
  onPick,
  picking,
  beforeReplaceBackground,
  onBackgroundUploaded,
  compact,
  lineBlocker,
  hintId,
  onNext,
}: {
  bookId: number;
  page: Page;
  background: Generation | undefined;
  pose: Generation | undefined;
  onPick: (kind: ThumbKind, g: Generation) => void;
  picking: boolean;
  beforeReplaceBackground: () => Promise<unknown>;
  onBackgroundUploaded: (page: Page) => void;
  compact: boolean;
  /** 不能進 ② 補線的原因（可以時 undefined）。 */
  lineBlocker: string | undefined;
  hintId: string;
  onNext: () => void;
}) {
  return (
    <>
      <ComposeWorkspace
        key={page.id}
        bookId={bookId}
        page={page}
        background={background}
        pose={pose}
        onPickBackground={(g) => onPick("background", g)}
        onPickPose={(g) => onPick("pose", g)}
        picking={picking}
        beforeReplaceBackground={beforeReplaceBackground}
        onBackgroundUploaded={onBackgroundUploaded}
        compact={compact}
      />
      <Button
        variant="primary"
        size="lg"
        disabled={!!lineBlocker}
        aria-describedby={lineBlocker ? hintId : undefined}
        onClick={onNext}
      >
        下一步：補線 →
      </Button>
      {lineBlocker && (
        <span id={hintId} className={styles.nextHint}>
          {lineBlocker}
        </span>
      )}
    </>
  );
}
