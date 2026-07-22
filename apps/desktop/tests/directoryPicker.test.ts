import { afterEach, expect, test } from "bun:test";

import { selectDirectory } from "@/features/sessions/lib/directoryPicker";

const originalWindow = globalThis.window;
const setWindow = (value: unknown) => {
  (globalThis as typeof globalThis & { window?: unknown }).window = value;
};

afterEach(() => {
  setWindow(originalWindow);
});

test("selectDirectory returns the native directory choice", async () => {
  const calls: unknown[] = [];
  setWindow({
    ardenDesktop: {
      dialog: {
        selectDirectory: async (options: unknown) => {
          calls.push(options);
          return "/Users/me/src/arden";
        },
      },
    },
  });

  await expect(selectDirectory({ defaultPath: "/Users/me" })).resolves.toBe("/Users/me/src/arden");
  expect(calls).toEqual([{ defaultPath: "/Users/me" }]);
});

test("selectDirectory returns null when the bridge is unavailable or cancelled", async () => {
  setWindow({});
  await expect(selectDirectory()).resolves.toBeNull();

  setWindow({
    ardenDesktop: {
      dialog: {
        selectDirectory: async () => null,
      },
    },
  });
  await expect(selectDirectory()).resolves.toBeNull();
});
