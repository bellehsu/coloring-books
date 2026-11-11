"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { notFound } from "next/navigation";
import { ApiError, type Invite } from "@/api/client";
import { useCreateInvite, useInvites } from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { Button } from "@/components/ui/Button";
import styles from "./InviteAdmin.module.css";

const NO_LINK = "這個連結無法再顯示，請用指令換發";
const DEFAULT_CAP = 60;

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function CopyLink({ invite }: { invite: Invite }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);
  if (invite.link === null) {
    return <span className={styles.nolink}>{invite.revoked ? "已撤銷，連結已失效" : NO_LINK}</span>;
  }
  const link = invite.link;
  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      return; // 沒有剪貼簿權限：網址仍顯示在旁邊，可以手動選取
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }
  return (
    <span className={styles.link}>
      <code className={styles.url}>{link}</code>
      <Button size="md" onClick={copy} aria-label={`複製 ${invite.name} 的連結`}>
        {copied ? "已複製" : "複製"}
      </Button>
    </span>
  );
}

export function InviteAdmin() {
  const invites = useInvites();
  const create = useCreateInvite();
  const [name, setName] = useState("");
  const [admin, setAdmin] = useState(false);
  const [cap, setCap] = useState(String(DEFAULT_CAP));

  if (invites.error instanceof ApiError && invites.error.status === 404) notFound();

  const trimmed = name.trim();
  const capNum = Number(cap);
  const capOk = admin || (Number.isInteger(capNum) && capNum >= 1 && capNum <= 1000);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!trimmed || !capOk || create.isPending) return;
    create.mutate(
      { name: trimmed, is_admin: admin, daily_generation_cap: admin ? DEFAULT_CAP : capNum },
      { onSuccess: () => setName("") },
    );
  }

  const rows = invites.data;
  return (
    <>
      <AppBar crumbs={[{ label: "邀請管理" }]} />
      <main className={styles.main}>
        <h1 className={styles.h1}>邀請管理</h1>

        <form className={styles.form} onSubmit={submit} aria-label="產生邀請連結">
          <label className={styles.field}>
            名字
            <input
              type="text"
              className={styles.input}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={40}
              autoComplete="off"
            />
          </label>
          <label className={styles.check}>
            <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
            管理者
          </label>
          {!admin && (
            <label className={styles.field}>
              每日上限
              <input
                type="number"
                className={`${styles.input} ${styles.cap}`}
                value={cap}
                onChange={(e) => setCap(e.target.value)}
                min={1}
                max={1000}
                inputMode="numeric"
              />
            </label>
          )}
          <Button type="submit" variant="primary" disabled={!trimmed || !capOk || create.isPending}>
            產生連結
          </Button>
        </form>
        {create.isError && (
          <p role="alert" className={styles.error}>
            {create.error instanceof ApiError ? create.error.message : "建立失敗"}
          </p>
        )}

        {invites.isPending && <div className={styles.skeleton} aria-busy="true" aria-label="載入中" />}
        {invites.isError && !(invites.error instanceof ApiError && invites.error.status === 404) && (
          <p role="alert" className={styles.error}>
            {invites.error instanceof ApiError ? invites.error.message : "無法載入邀請"}
          </p>
        )}
        {rows && (
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">名字</th>
                <th scope="col">身分</th>
                <th scope="col">每日上限</th>
                <th scope="col">今天</th>
                <th scope="col">書</th>
                <th scope="col">最後使用</th>
                <th scope="col">狀態</th>
                <th scope="col">連結</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={r.revoked ? styles.revoked : undefined}>
                  <th scope="row" data-label="名字" className={styles.name}>
                    {r.name}
                  </th>
                  <td data-label="身分">{r.is_admin ? "管理者" : "使用者"}</td>
                  <td data-label="每日上限">{r.daily_generation_cap ?? "不限"}</td>
                  <td data-label="今天">{r.today}</td>
                  <td data-label="書">{r.book_count}</td>
                  <td data-label="最後使用">{formatTime(r.last_seen)}</td>
                  <td data-label="狀態">{r.revoked ? "已撤銷" : "使用中"}</td>
                  <td data-label="連結" className={styles.linkCell}>
                    <CopyLink invite={r} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className={styles.note}>
          換發連結、撤銷、修改每日上限與刪除資料，請在伺服器上用 <code>scripts/invites.py</code> 操作。
        </p>
      </main>
    </>
  );
}
