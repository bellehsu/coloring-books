"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import styles from "./page.module.css";

type Peek = { state: "loading" } | { state: "invalid" } | { state: "ok"; token: string; name: string };

/** 已經登入成另一個人時開了邀請連結（後端 GET /i/{token} 轉來，token 在 #fragment，不會送到伺服器）。
 *  先問過再切換，避免別人傳來的連結直接換掉這個瀏覽器的身分。 */
export default function SwitchAccountPage() {
  const me = useMe();
  const [peek, setPeek] = useState<Peek>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      const token = window.location.hash.replace(/^#/, "");
      if (!token) return setPeek({ state: "invalid" });
      try {
        const res = await fetch("/api/invites/peek", { headers: { "X-Invite-Token": token } });
        if (cancelled) return;
        if (!res.ok) return setPeek({ state: "invalid" });
        const body = (await res.json()) as { name: string };
        setPeek({ state: "ok", token, name: body.name });
      } catch {
        if (!cancelled) setPeek({ state: "invalid" });
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  if (peek.state === "invalid") {
    return (
      <main className={styles.main}>
        <div className={styles.card}>
          <h1 className={styles.h1}>這個連結已經失效</h1>
          <Link href="/" className={styles.link}>
            回首頁
          </Link>
        </div>
      </main>
    );
  }
  if (peek.state === "loading" || !me.data) {
    return <main className={styles.main} />;
  }

  const switchTo = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post("/invites/accept", { token: peek.token });
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- 整頁重新載入，快取全部重來
      window.location.assign("/");
    } catch {
      setError("切換失敗，請再試一次");
      setBusy(false);
    }
  };

  return (
    <main className={styles.main}>
      <div className={styles.card}>
        <h1 className={styles.h1}>要切換身分嗎？</h1>
        <p className={styles.text}>
          這個連結是「{peek.name}」的。要切換成「{peek.name}」嗎？目前是「{me.data.name}」。
        </p>
        {error && <p className={styles.text} role="alert">{error}</p>}
        <div className={styles.actions}>
          <Button variant="primary" size="lg" onClick={switchTo} disabled={busy}>
            切換
          </Button>
          {/* eslint-disable-next-line @next/next/no-location-assign-relative-destination -- 整頁重新載入 */}
          <Button size="lg" onClick={() => window.location.assign("/")} disabled={busy}>
            留在目前的身分
          </Button>
        </div>
      </div>
    </main>
  );
}
