import { spawn } from "node:child_process";

const devUrl = "http://127.0.0.1:5175";
const children = new Set();

function run(command, args, options = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
    ...options,
  });
  children.add(child);
  child.on("exit", () => children.delete(child));
  return child;
}

async function waitForVite() {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(devUrl);
      if (response.ok) return;
    } catch {
      await new Promise(resolve => setTimeout(resolve, 150));
    }
  }
  throw new Error(`Vite did not start at ${devUrl}`);
}

async function shutdown(code = 0) {
  // Send SIGTERM to children and wait up to 3s for graceful exit before
  // tearing the parent down. Critical for the Electron child: Chromium
  // flushes localStorage to disk during its shutdown sequence, and
  // exiting the parent first kills the renderer mid-flush — which is
  // why dev-mode setting changes used to vanish across hard reloads.
  const exits = [];
  for (const child of children) {
    if (child.exitCode !== null || child.killed) continue;
    exits.push(new Promise((resolve) => child.once("exit", resolve)));
    child.kill("SIGTERM");
  }
  await Promise.race([
    Promise.all(exits),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  process.exit(code);
}

process.on("SIGINT", () => { void shutdown(0); });
process.on("SIGTERM", () => { void shutdown(0); });

const vite = run("vite", ["--host", "127.0.0.1", "--port", "5175", "--strictPort"]);

try {
  await waitForVite();
} catch (error) {
  console.error(error);
  shutdown(1);
}

const electron = run("electron", ["."], {
  env: {
    ...process.env,
    ARDEN_DESKTOP_DEV_SERVER_URL: devUrl,
  },
});

electron.on("exit", code => shutdown(code ?? 0));
vite.on("exit", code => {
  if (code !== 0) shutdown(code ?? 1);
});
