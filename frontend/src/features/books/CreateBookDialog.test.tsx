import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreateBookDialog } from "./CreateBookDialog";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CreateBookDialog open onClose={() => {}} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  push.mockReset();
});

describe("CreateBookDialog", () => {
  it("書名空白時不能送出", async () => {
    setup();
    const submit = screen.getByRole("button", { name: /建立，接著設定角色/ });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("書名"), "   ");
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("書名"), "x");
    expect(submit).toBeEnabled();
  });

  it("預設 A4＋印刷廠，改成 Letter＋家用顯示 896 × 1152", async () => {
    setup();
    expect(screen.getByText("864 × 1216")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Letter/ }));
    await userEvent.click(screen.getByRole("radio", { name: /家用印表機/ }));
    expect(screen.getByText("896 × 1152")).toBeInTheDocument();
    expect(screen.getByText("19.6 × 25.9 cm")).toBeInTheDocument();
  });

  it("送出呼叫 POST /api/books 並導到角色工作室", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 7 }), { status: 201, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setup();
    await userEvent.type(screen.getByLabelText("書名"), "  小兔子 ");
    await userEvent.click(screen.getByRole("radio", { name: /Letter/ }));
    await userEvent.click(screen.getByRole("radio", { name: /家用印表機/ }));
    await userEvent.click(screen.getByRole("button", { name: /建立，接著設定角色/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/7/character"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/books");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ title: "小兔子", paper_size: "letter", print_mode: "home" });
  });

  it("後端錯誤顯示在對話框內", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "書名重複" }), { status: 422, headers: { "content-type": "application/json" } }),
      ),
    );
    setup();
    await userEvent.type(screen.getByLabelText("書名"), "a");
    await userEvent.click(screen.getByRole("button", { name: /建立，接著設定角色/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("書名重複");
    expect(push).not.toHaveBeenCalled();
  });
});
