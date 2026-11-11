"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { onApiError } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

/** 後端 401 的 detail（backend/app/api/deps.py）：這個瀏覽器沒有（或只有過期的）session；連結本身可能還有效 */
const NEED_INVITE = "請用邀請連結開啟";

/** 401 → /invalid-link：沒有 session 是 reason=missing（請再開一次邀請連結），其他（owner 被撤銷）是 reason=revoked。
 *  429 不在這裡處理：各呼叫端已用 errMsg(e) 顯示後端訊息，全域再顯示會重複。 */
function GlobalApiErrors() {
  const router = useRouter();
  useEffect(
    () =>
      onApiError((err) => {
        if (err.status !== 401 || window.location.pathname === "/invalid-link") return;
        router.replace(`/invalid-link?reason=${err.detail === NEED_INVITE ? "missing" : "revoked"}`);
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
