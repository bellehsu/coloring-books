"use client";

import { use } from "react";
import { notFound } from "next/navigation";
import { SharedBookView } from "@/features/shared/SharedBookView";

export default function SharedBookRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const bookId = Number(id);
  if (!Number.isInteger(bookId) || bookId < 1) notFound();
  return <SharedBookView bookId={bookId} />;
}
