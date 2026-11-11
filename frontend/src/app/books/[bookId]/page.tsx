"use client";

import { use } from "react";
import { notFound } from "next/navigation";
import { BookOverview } from "@/features/book/BookOverview";

/** B 整本書（spec §4.3）。舊的頁面編輯器在 /books/{id}/editor（「進階編輯」）。 */
export default function BookRoute({ params }: { params: Promise<{ bookId: string }> }) {
  const { bookId } = use(params);
  const id = Number(bookId);
  if (!Number.isInteger(id) || id < 1) notFound();
  return <BookOverview bookId={id} />;
}
