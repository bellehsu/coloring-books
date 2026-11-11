import type { components } from "./schema";

type S = components["schemas"];
export type Book = S["BookOut"];
export type SharedBook = S["SharedBookOut"];
export type SharedBookDetail = S["SharedBookDetailOut"];
export type Me = S["MeOut"];
export type Invite = S["InviteOut"];
export type InviteCreate = S["InviteCreate"];
export type Page = S["PageOut"];
export type Generation = S["GenerationOut"];
export type QA = S["QAOut"];
export type Edits = S["EditsOut"];
export type QueueStatus = S["QueueStatus"];
export type BatchMaterials = S["BatchMaterialsOut"];
export type BookOverview = S["BookOverviewOut"];
export type OverviewPage = S["OverviewPageOut"];
export type ProducePlan = S["ProducePlanOut"];
export type ProduceResult = S["ProduceOut"];
/** 角色在背景上的位置（相對背景）：cx、cy 為角色中心 0–1，height 為角色高／背景高 0.1–1.0。 */
export interface Placement {
  cx: number;
  cy: number;
  height: number;
  flip: boolean;
}
/** 同後端預設：中心 0.66、高 0.6 → 底緣 0.96，貼近下方。 */
export const DEFAULT_PLACEMENT: Placement = { cx: 0.5, cy: 0.66, height: 0.6, flip: false };
/** 後端 export/summary 沒有 response model，型別在此手寫（見 backend/app/api/export.py）。 */
export interface ExportSummary {
  total: number;
  passed: number;
  exportable_ids: number[];
  skipped: { page_id: number; position: number; reason: "no_selection" | "not_passed" }[];
}

const BASE = "/api";

function messageFrom(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const parts = detail
      .map((d) => {
        if (!d || typeof d !== "object") return null;
        const { loc, msg } = d as { loc?: unknown; msg?: unknown };
        if (typeof msg !== "string") return null;
        const field = Array.isArray(loc) ? String(loc[loc.length - 1] ?? "") : "";
        return field ? `${field}：${msg}` : msg;
      })
      .filter((p): p is string => p !== null);
    if (parts.length > 0) return parts.join("；");
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const m = (detail as { message?: unknown }).message;
    if (typeof m === "string" && m) return m;
  }
  return `伺服器錯誤（${status}）`;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(messageFrom(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function failure(res: Response): Promise<ApiError> {
  let detail: unknown;
  try {
    detail = ((await res.json()) as { detail?: unknown })?.detail;
  } catch {
    detail = undefined;
  }
  return new ApiError(res.status, detail);
}

type ApiErrorListener = (err: ApiError) => void;
const errorListeners = new Set<ApiErrorListener>();

/** 全域 API 錯誤通知（401 轉址、429 toast 等）；回傳取消訂閱函式。錯誤照常丟給呼叫端。 */
export function onApiError(fn: ApiErrorListener): () => void {
  errorListeners.add(fn);
  return () => {
    errorListeners.delete(fn);
  };
}

async function request(path: string, init: RequestInit): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const err = await failure(res);
    errorListeners.forEach((fn) => fn(err));
    throw err;
  }
  return res;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit & { headers: Record<string, string> } = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await request(path, init);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* 落到 filename= */
    }
  }
  const plain = /filename\s*=\s*"([^"]*)"/i.exec(disposition) ?? /filename\s*=\s*([^;]+)/i.exec(disposition);
  return plain ? plain[1].trim() : null;
}

export const api = {
  get: <T>(path: string) => json<T>("GET", path),
  post: <T>(path: string, body?: unknown) => json<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => json<T>("PATCH", path, body),
  put: <T>(path: string, body?: unknown) => json<T>("PUT", path, body),
  /** 204 回 undefined；有 JSON body 則回傳之。 */
  del: <T = void>(path: string) => json<T>("DELETE", path),
  /** multipart 上傳（欄位名 `file`），回 JSON；非 2xx 丟 ApiError（後端的中文訊息）。 */
  async upload<T>(path: string, file: Blob): Promise<T> {
    const form = new FormData();
    form.append("file", file, file instanceof File && file.name ? file.name : "file.png");
    const res = await request(path, { method: "POST", body: form });
    return (await res.json()) as T;
  },
  /** 下載檔案：回 Blob 與 Content-Disposition 的檔名（沒有則 null）。非 2xx 丟 ApiError。 */
  async download(path: string): Promise<{ blob: Blob; filename: string | null }> {
    const res = await request(path, { method: "GET" });
    return { blob: await res.blob(), filename: filenameFrom(res.headers.get("content-disposition")) };
  },
};
