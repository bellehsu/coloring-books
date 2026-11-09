"use client";

import { use } from "react";
import { notFound } from "next/navigation";
import { CharacterStudio } from "@/features/character/CharacterStudio";

export default function CharacterPage({ params }: { params: Promise<{ bookId: string }> }) {
  const { bookId } = use(params);
  const id = Number(bookId);
  if (!Number.isInteger(id) || id < 1) notFound();
  return <CharacterStudio bookId={id} />;
}
