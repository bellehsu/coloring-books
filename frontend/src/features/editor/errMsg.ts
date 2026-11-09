import { ApiError } from "@/api/client";

/** 後端給的中文訊息（ApiError）；其他錯誤用 fallback。 */
export const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
