"use client";

import { SetupPage } from "@/features/setup/SetupPage";

/** A 做一本新的著色書（spec §4.5）：書在 ① 填好時建立，網址接著換成 /books/{id}/setup。 */
export default function NewBookRoute() {
  return <SetupPage bookId={null} />;
}
