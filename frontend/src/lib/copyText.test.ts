import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./copyText";

function env(opts: { secure: boolean; writeText?: () => Promise<void>; exec?: () => boolean }) {
  Object.defineProperty(window, "isSecureContext", { value: opts.secure, configurable: true });
  const writeText = vi.fn(opts.writeText ?? (async () => {}));
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const exec = vi.fn(opts.exec ?? (() => true));
  (document as unknown as { execCommand: unknown }).execCommand = exec;
  return { writeText, exec };
}

beforeEach(() => {
  document.body.innerHTML = "";
});
afterEach(() => {
  Reflect.deleteProperty(navigator, "clipboard");
  Reflect.deleteProperty(window, "isSecureContext");
  Reflect.deleteProperty(document, "execCommand");
});

describe("copyText", () => {
  it("secure + clipboard ok -> true, no execCommand", async () => {
    const { writeText, exec } = env({ secure: true });
    expect(await copyText("hi")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("hi");
    expect(exec).not.toHaveBeenCalled();
  });

  it("secure + clipboard rejects -> falls back to execCommand('copy')", async () => {
    const { exec } = env({ secure: true, writeText: async () => Promise.reject(new Error("denied")) });
    expect(await copyText("hi")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it("insecure -> never touches navigator.clipboard", async () => {
    const { writeText, exec } = env({ secure: false });
    expect(await copyText("hi")).toBe(true);
    expect(writeText).not.toHaveBeenCalled();
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it.each([
    ["returns false", () => false],
    [
      "throws",
      () => {
        throw new Error("nope");
      },
    ],
  ])("execCommand %s -> false, textarea removed, focus restored", async (_n, exec) => {
    env({ secure: false, exec });
    const btn = document.createElement("button");
    document.body.appendChild(btn);
    btn.focus();
    expect(await copyText("hi")).toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(btn);
  });
});
