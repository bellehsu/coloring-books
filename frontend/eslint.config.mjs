import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // 進階編輯（features/editor）是可以整個拿掉的一層：C（features/fix）與共用的編輯元件（features/editing）不能依賴它
    files: ["src/features/fix/**", "src/features/editing/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              // 別名（@/features/editor）與任何深度的相對路徑（../editor、../../editor/x）都擋
              regex: "^(@/features/editor|(\\.\\./)+(features/)?editor)(/|$)|(^|/)features/editor(/|$)",
              message: "features/fix 與 features/editing 不能 import features/editor（共用的放 features/editing）。",
            },
          ],
        },
      ],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "src/api/schema.d.ts", "playwright-report/**", "test-results/**"]),
]);
