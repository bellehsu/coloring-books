import type { NextConfig } from "next";

// next dev 預設只允許 localhost 存取 /_next/*（含 HMR WebSocket）；從區網其他電腦開開發伺服器時，
// 要把瀏覽器網址列用的主機名稱／IP 列進來（逗號分隔）。scripts/dev.sh 會自動帶入本機主機名稱與 IP。
// 只影響開發模式；正式版（next build + start）不受影響。
const allowedDevOrigins = (process.env.DEV_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

const nextConfig: NextConfig = {
  output: "standalone",
  agentRules: false, // 不讓 next dev 自動產生 AGENTS.md
  allowedDevOrigins,
  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
    // 邀請連結 /i/{token} 也走後端：後端設定 cb_session cookie 後轉回 `/`，與 /api 同源，cookie 才帶得到。
    // 只轉後端有的形狀（剛好一段 token）；其他形狀見 redirects
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/i/:token", destination: `${backend}/i/:token` },
    ];
  },
  async redirects() {
    // 被聊天 App 截斷（/i、/i/）或多出段落（/i/a/b）的邀請連結：顯示「連結已失效」頁，而不是後端的 JSON 404
    return [
      { source: "/i", destination: "/invalid-link", permanent: false },
      { source: "/i/:token/:rest+", destination: "/invalid-link", permanent: false },
      // Phase 13：舊的角色、故事頁併進 A「主角與故事」
      { source: "/books/:bookId/character", destination: "/books/:bookId/setup", permanent: false },
      { source: "/books/:bookId/story", destination: "/books/:bookId/setup", permanent: false },
    ];
  },
};

export default nextConfig;
