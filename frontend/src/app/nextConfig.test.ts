import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";

describe("next.config rewrites", () => {
  it("/api 與邀請連結 /i/{token} 都轉給後端（同源，cookie 才帶得到）", async () => {
    const prev = process.env.BACKEND_URL;
    process.env.BACKEND_URL = "http://backend.test:8010";
    try {
      const rewrites = await nextConfig.rewrites!();
      expect(rewrites).toEqual([
        { source: "/api/:path*", destination: "http://backend.test:8010/api/:path*" },
        // 只轉後端有的形狀（一段 token）；/i、/i/a/b 不轉，由 redirects 送到 /invalid-link
        { source: "/i/:token", destination: "http://backend.test:8010/i/:token" },
      ]);
    } finally {
      if (prev === undefined) delete process.env.BACKEND_URL;
      else process.env.BACKEND_URL = prev;
    }
  });

  it("截斷或多出段落的邀請連結轉到 /invalid-link（不顯示後端的 JSON 404）", async () => {
    expect(await nextConfig.redirects!()).toEqual([
      { source: "/i", destination: "/invalid-link", permanent: false },
      { source: "/i/:token/:rest+", destination: "/invalid-link", permanent: false },
    ]);
  });
});
