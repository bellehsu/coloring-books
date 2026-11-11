"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { onApiError } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

/** 401（連結失效或被撤銷）→ /invalid-link。429 不在這裡處理：各呼叫端已用 errMsg(e) 顯示後端訊息，全域再顯示會重複。 */
function GlobalApiErrors() {
  const router = useRouter();
  useEffect(
    () =>
      onApiError((err) => {
        if (err.status === 401 && window.location.pathname !== "/invalid-link") router.replace("/invalid-link");
      }),
    [router],
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
