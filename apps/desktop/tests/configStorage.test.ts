import { afterEach, expect, test } from "bun:test";
import { loadInitialConfig, saveConfig, STORAGE_KEY } from "@/api/core";

class MemoryStorage {
  private values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

const originalWindow = globalThis.window;
const originalLocalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

function installBrowserFallback() {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storage,
  });
  // A browser window without the Electron bridge — exercises the localStorage path.
  (globalThis as typeof globalThis & { window?: unknown }).window = {};
  return storage;
}

afterEach(() => {
  if (originalLocalStorage) Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
  else Reflect.deleteProperty(globalThis, "localStorage");
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
});

test("persists browser fallback connection config", async () => {
  const storage = installBrowserFallback();

  const saved = await saveConfig({
    serverUrl: "http://127.0.0.1:6877/",
    apiKey: "arden_test",
  });

  expect(saved).toEqual({ serverUrl: "http://127.0.0.1:6877", apiKey: "arden_test" });
  expect(storage.getItem(STORAGE_KEY)).toBe(JSON.stringify(saved));
  expect(await loadInitialConfig()).toEqual(saved);
});
