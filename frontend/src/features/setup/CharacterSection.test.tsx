import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Book, Generation } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { politeToasts } from "@/components/ui/toastTesting";
import { CharacterSection } from "./CharacterSection";

const gen = (id: number, status: string): Generation =>
  ({ id, status, kind: "character", discarded: false, image_url: status === "succeeded" ? `/img/${id}.png` : null, user_text: "兔子", queue_position: null, error: null }) as unknown as Generation;
const book = { id: 1, title: "書", character_generation_id: 11, character_description: "兔子" } as unknown as Book;

function setup(gens: Generation[]) {
  const calls: [string, RequestInit][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push([url, init]);
      if (init.method === "DELETE") return new Response(null, { status: 204 });
      if (init.method === "PATCH") return new Response(JSON.stringify(gens[1]), { status: 200, headers: { "content-type": "application/json" } });
      return new Response(JSON.stringify(gens), { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <CharacterSection bookId={1} book={book} titleValid ensureBook={async () => 1} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

describe("CharacterSection ×", () => {
  it("the chosen candidate has no ×, others do (also failed); a queued one has none", async () => {
    setup([gen(11, "succeeded"), gen(12, "succeeded"), gen(13, "failed"), gen(14, "queued")]);
    await screen.findByAltText("主角候選 2");
    expect(screen.queryByRole("button", { name: "刪掉候選 1" })).toBeNull();
    expect(screen.getByRole("button", { name: "刪掉候選 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刪掉候選 3" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "刪掉候選 4" })).toBeNull();
  });

  it("× sends DELETE and the undo toast restores", async () => {
    const calls = setup([gen(11, "succeeded"), gen(12, "succeeded")]);
    await userEvent.click(await screen.findByRole("button", { name: "刪掉候選 2" }));
    await waitFor(() => expect(politeToasts()).toHaveTextContent("已刪掉這張候選"));
    const del = calls.find(([, i]) => i.method === "DELETE")!;
    expect(del[0]).toBe("/api/books/1/character/candidates/12");
    await userEvent.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(calls.some(([, i]) => i.method === "PATCH")).toBe(true));
    const patch = calls.find(([, i]) => i.method === "PATCH")!;
    expect(patch[0]).toBe("/api/generations/12");
    expect(JSON.parse(patch[1].body as string)).toEqual({ discarded: false });
  });
});
