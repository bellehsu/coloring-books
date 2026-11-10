export type EtaKind = "character" | "background" | "pose";

/** 沒有歷史耗時時，各類型每張的估計秒數。 */
const FALLBACK_S: Record<EtaKind, number> = { character: 20, background: 20, pose: 25 };
const WINDOW = 10;

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * 估計排隊中的工作還要多少秒：每個工作用同類型「最近 10 筆」耗時的中位數，沒有紀錄就用預設值。
 * `recent` 需由新到舊排列。
 */
export function estimateSeconds(
  pending: { kind: EtaKind }[],
  recent: { kind: string; elapsed_s: number }[],
): number {
  const perKind = new Map<string, number>();
  const unit = (kind: EtaKind): number => {
    let v = perKind.get(kind);
    if (v === undefined) {
      const xs = recent.filter((r) => r.kind === kind).slice(0, WINDOW).map((r) => r.elapsed_s);
      v = xs.length ? median(xs) : FALLBACK_S[kind];
      perKind.set(kind, v);
    }
    return v;
  };
  return pending.reduce((sum, p) => sum + unit(p.kind), 0);
}

/** 「剩約 X 分鐘」／「剩不到 1 分鐘」。 */
export function formatRemaining(seconds: number): string {
  if (seconds < 60) return "剩不到 1 分鐘";
  return `剩約 ${Math.ceil(seconds / 60)} 分鐘`;
}
