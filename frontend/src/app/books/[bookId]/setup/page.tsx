"use client";

import { use } from "react";
import { notFound } from "next/navigation";
import { SetupPage } from "@/features/setup/SetupPage";

/** A 的同一頁，給已經有的書（「主角與故事」）。舊的 /character、/story 轉到這裡（next.config redirects）。 */
export default function SetupRoute({ params }: { params: Promise<{ bookId: string }> }) {
  const { bookId } = use(params);
  const id = Number(bookId);
  if (!Number.isInteger(id) || id < 1) notFound();
  return <SetupPage key={id} bookId={id} />;
}
