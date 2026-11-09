import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./client";

function jsonRes(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("ApiError", () => {
  it("uses a string detail as the message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(409, { detail: "請先設定角色" })));
    const err = (await api.get("/books").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("請先設定角色");
  });

  it("uses detail.message for the pass 409 shape", async () => {
    const detail = { message: "線條有缺口", errors: [{ code: "gap", message: "x" }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(409, { detail })));
    const err = (await api.post("/pages/1/pass", {}).catch((e) => e)) as ApiError;
    expect(err.message).toBe("線條有缺口");
    expect(err.detail).toEqual(detail);
  });

  it("joins 422 validation arrays as field：reason", async () => {
    const detail = [
      { loc: ["body", "count"], msg: "必須大於 0", type: "x" },
      { loc: ["body", "title"], msg: "必填", type: "y" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(422, { detail })));
    const err = (await api.post("/books", {}).catch((e) => e)) as ApiError;
    expect(err.message).toBe("count：必須大於 0；title：必填");
  });

  it("falls back to a generic message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("boom", { status: 502 })));
    const err = (await api.get("/books").catch((e) => e)) as ApiError;
    expect(err.message).toBe("伺服器錯誤（502）");
  });
});

describe("api", () => {
  it("prefixes /api and sends JSON bodies", async () => {
    const f = vi.fn().mockResolvedValue(jsonRes(200, { ok: 1 }));
    vi.stubGlobal("fetch", f);
    await api.post("/books", { title: "a" });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/books");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"title":"a"}');
  });

  it("del tolerates 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api.del("/pages/1")).resolves.toBeUndefined();
  });

  it("upload sends FormData with field file", async () => {
    const f = vi.fn().mockResolvedValue(jsonRes(200, {}));
    vi.stubGlobal("fetch", f);
    const blob = new Blob(["x"], { type: "image/png" });
    await api.upload("/pages/1/sketch", blob);
    const [, init] = f.mock.calls[0];
    expect(init.method).toBe("PUT");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(Blob);
    expect(init.headers?.["Content-Type"]).toBeUndefined();
  });

  it("blob returns a Blob", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("png", { status: 200 })));
    const b = await api.blob("/books/1/reference-sketch", "POST");
    expect(b).toBeInstanceOf(Blob);
  });
});

describe("api.download", () => {
  function pdfRes(disposition?: string) {
    return new Response(new Blob(["%PDF"]), {
      status: 200,
      headers: disposition ? { "content-disposition": disposition } : {},
    });
  }

  it("filename* 解碼 UTF-8", async () => {
    const d = `attachment; filename="book-1.pdf"; filename*=UTF-8''${encodeURIComponent("小兔子的一天")}.pdf`;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pdfRes(d)));
    const r = await api.download("/books/1/export.pdf");
    expect(r.filename).toBe("小兔子的一天.pdf");
    expect(r.blob.size).toBeGreaterThan(0);
  });

  it("沒有 filename* 時用 filename，都沒有時為 null", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pdfRes('attachment; filename="book-1.pdf"')));
    expect((await api.download("/x")).filename).toBe("book-1.pdf");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pdfRes()));
    expect((await api.download("/x")).filename).toBeNull();
  });

  it("409 丟出帶後端訊息的 ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes(409, { detail: "沒有可匯出的頁面" })));
    const err = (await api.download("/x").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("沒有可匯出的頁面");
  });
});
