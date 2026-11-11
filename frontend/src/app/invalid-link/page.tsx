import type { Metadata } from "next";
import styles from "./page.module.css";

export const metadata: Metadata = { title: "連結已失效" };

/** 邀請連結無效或已撤銷時，後端 /i/{token} 轉到這裡。不呼叫任何 API（這時沒有有效的登入）。 */
export default function InvalidLinkPage() {
  return (
    <main className={styles.main}>
      <div className={styles.card}>
        <h1 className={styles.h1}>連結已失效</h1>
        <p className={styles.text}>連結已失效，請向邀請你的人要新的連結</p>
      </div>
    </main>
  );
}
