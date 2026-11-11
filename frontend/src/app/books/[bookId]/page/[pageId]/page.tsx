"use client";

import { Suspense, use } from "react";
import { notFound, useSearchParams } from "next/navigation";
import { FixPage } from "@/features/fix/FixPage";
import { parseFilter } from "@/features/fix/fixState";

/** C 處理一頁（spec §4.4）。?filter=attention：從 B 的「請看一下」打開。 */
export default function FixRoute({ params }: { params: Promise<{ bookId: string; pageId: string }> }) {
  const { bookId, pageId } = use(params);
  const book = Number(bookId);
  const page = Number(pageId);
  if (!Number.isInteger(book) || book < 1 || !Number.isInteger(page) || page < 1) notFound();
  // useSearchParams 需要 Suspense 邊界
  return (
    <Suspense fallback={null}>
      <FixWithFilter bookId={book} pageId={page} />
    </Suspense>
  );
}

function FixWithFilter({ bookId, pageId }: { bookId: number; pageId: number }) {
  const filter = parseFilter(useSearchParams().get("filter"));
  // key：換頁時（上一頁／下一頁）狀態（預覽、畫筆、展開的欄位）重新開始
  return <FixPage key={pageId} bookId={bookId} pageId={pageId} filter={filter} />;
}
