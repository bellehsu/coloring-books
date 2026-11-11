import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";

describe("next.config rewrites", () => {
  it("/api 與邀請連結 /i 都轉給後端（同源，cookie 才帶得到）", async () => {
    const prev = process.env.BACKEND_URL;
    process.env.BACKEND_URL = "http://backend.test:8010";
    try {
      const rewrites = await nextConfig.rewrites!();
      expect(rewrites).toEqual([
        { source: "/api/:path*", destination: "http://backend.test:8010/api/:path*" },
        { source: "/i/:path*", destination: "http://backend.test:8010/i/:path*" },
      ]);
    } finally {
      if (prev === undefined) delete process.env.BACKEND_URL;
      else process.env.BACKEND_URL = prev;
    }
  });
});
