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
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
