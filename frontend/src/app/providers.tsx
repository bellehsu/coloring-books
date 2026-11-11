"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { onApiError } from "@/api/client";
import { ToastProvider, useToast } from "@/components/ui/Toast";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

/** 401（連結失效或被撤銷）→ /invalid-link；429（今日上限）→ 用 toast 顯示後端訊息。 */
function GlobalApiErrors() {
  const toast = useToast();
  const router = useRouter();
  useEffect(
    () =>
      onApiError((err) => {
        if (err.status === 401) {
          if (window.location.pathname !== "/invalid-link") router.replace("/invalid-link");
        } else if (err.status === 429) {
          toast.show(err.message, "error");
        }
      }),
    [toast, router],
  );
  return null;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <GlobalApiErrors />
        {children}
      </ToastProvider>
    </QueryClientProvider>
  );
}
