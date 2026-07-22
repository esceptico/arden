#!/usr/bin/env node

const [major = 0, minor = 0, patch = 0] = process.versions.node
  .split(".")
  .map((part) => Number.parseInt(part, 10));

const viteCompatible =
  (major === 20 && minor >= 19) ||
  (major === 22 && minor >= 12) ||
  major > 22;

if (!viteCompatible) {
  console.error(
    `Arden desktop requires Node ^20.19.0 or >=22.12.0 for Vite 7. Current: ${major}.${minor}.${patch}`,
  );
  console.error("Use a Node version manager from apps/desktop/.node-version, or run with `npx -y node@22.12.0 ...`.");
  process.exit(1);
}
