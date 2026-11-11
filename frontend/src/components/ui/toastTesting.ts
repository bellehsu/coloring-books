import { screen } from "@testing-library/react";

/**
 * 測試用：toast 放在 ToastProvider 常駐的兩個 live region 裡（沒有 role，不能用 getByRole 找）。
 * polite：一般與成功的訊息；assertive：錯誤。
 */
export const politeToasts = () => screen.getByTestId("toast-polite");
export const alertToasts = () => screen.getByTestId("toast-assertive");
