"use client";

import { use } from "react";
import { notFound } from "next/navigation";
import { StoryPage } from "@/features/story/StoryPage";

export default function StoryRoute({ params }: { params: Promise<{ bookId: string }> }) {
  const { bookId } = use(params);
  const id = Number(bookId);
  if (!Number.isInteger(id) || id < 1) notFound();
  return <StoryPage bookId={id} />;
}
