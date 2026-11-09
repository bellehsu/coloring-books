"use client";

import { Suspense, use } from "react";
import { notFound } from "next/navigation";
import { EditorPage } from "@/features/editor/EditorPage";

export default function EditorRoute({ params }: { params: Promise<{ bookId: string }> }) {
  const { bookId } = use(params);
  const id = Number(bookId);
  if (!Number.isInteger(id) || id < 1) notFound();
  // EditorPage 讀 useSearchParams（?page=、?mode=），需要 Suspense 邊界
  return (
    <Suspense fallback={null}>
      <EditorPage bookId={id} />
    </Suspense>
  );
}
