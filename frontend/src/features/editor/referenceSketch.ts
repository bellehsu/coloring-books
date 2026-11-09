import { ApiError } from "@/api/client";

/**
 * POST /books/{id}/reference-sketch（multipart 欄位 file）→ 擷取好的白底黑線 PNG。
 * api.upload 只回 JSON、api.blob 只送 JSON，所以這裡直接用 fetch，錯誤一樣轉成 ApiError（後端中文訊息）。
 */
export async function uploadReference(bookId: number, file: File): Promise<Blob> {
  const form = new FormData();
  form.append("file", file, file.name || "reference.png");
  const res = await fetch(`/api/books/${bookId}/reference-sketch`, { method: "POST", body: form });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = ((await res.json()) as { detail?: unknown })?.detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}
