import type { Metadata } from "next";
import styles from "./page.module.css";

type Props = { searchParams: Promise<{ reason?: string | string[] }> };

/** reason=missing：這個瀏覽器沒有 session（後端 401「請用邀請連結開啟」，例如換了瀏覽器或 App），連結本身還有效。
 *  其他（預設，含後端 /i/{token} 無效或撤銷時轉來的）：連結已失效。 */
const COPY = {
  missing: {
    title: "請用邀請連結開啟",
    text: "這個瀏覽器還沒開過邀請連結。請用邀請你的人給的連結開啟（換瀏覽器或 App 時要再開一次連結）。",
  },
  revoked: { title: "連結已失效", text: "連結已失效，請向邀請你的人要新的連結" },
} as const;

async function copyFor(searchParams: Props["searchParams"]) {
  const { reason } = await searchParams;
  return reason === "missing" ? COPY.missing : COPY.revoked;
}

export async function generateMetadata({ searchParams }: Props): Promise<Metadata> {
  return { title: (await copyFor(searchParams)).title };
}

/** 邀請連結無效、已撤銷，或這個瀏覽器還沒登入時來到這裡。不呼叫任何 API（這時沒有有效的登入）。 */
export default async function InvalidLinkPage({ searchParams }: Props) {
  const copy = await copyFor(searchParams);
  return (
    <main className={styles.main}>
      <div className={styles.card}>
        <h1 className={styles.h1}>{copy.title}</h1>
        <p className={styles.text}>{copy.text}</p>
      </div>
    </main>
  );
}
