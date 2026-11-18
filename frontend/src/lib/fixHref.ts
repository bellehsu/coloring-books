/** C（處理一頁）的網址：B、進階編輯、匯出都會連過去，所以放在 lib，不放在 features/fix。 */

/** C 是從 B 的哪個篩選打開的：「請看一下」時上一頁／下一頁只在要看的頁之間跳。 */
export type FixFilter = "attention" | null;

export const parseFilter = (v: string | null | undefined): FixFilter => (v === "attention" ? "attention" : null);

export function fixHref(bookId: number, pageId: number, filter: FixFilter = null): string {
  return `/books/${bookId}/page/${pageId}${filter ? `?filter=${filter}` : ""}`;
}
