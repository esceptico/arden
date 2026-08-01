// Device-skill advertisement: scans the user's skill directories and tells
// the server what exists as {name, description, sha256}; the server replies
// with the names whose SKILL.md body it still needs. Auxiliary skill files
// (references/, scripts/) are never synced — the agent reads them through
// the device read_file tool where they live.
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");

const SKILL_NAME_RE = /^[a-z][a-z0-9-]{0,47}$/;
const MAX_SCAN_DEPTH = 5;

function parseFrontmatter(content) {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return null;
  const fields = {};
  for (const line of match[1].split("\n")) {
    const kv = line.match(/^([A-Za-z_-]+):\s*(.*)$/);
    if (!kv) continue;
    fields[kv[1]] = kv[2].trim().replace(/^["']|["']$/g, "");
  }
  return fields;
}

async function collectSkillFiles(root, depth = 0) {
  if (depth > MAX_SCAN_DEPTH) return [];
  let entries;
  try {
    entries = await fs.readdir(root, { withFileTypes: true });
  } catch {
    return [];
  }
  const found = [];
  for (const entry of entries) {
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) {
      found.push(...(await collectSkillFiles(full, depth + 1)));
    } else if (entry.name === "SKILL.md") {
      found.push(full);
    }
  }
  return found;
}

async function scanDeviceSkills(roots) {
  const skills = new Map();
  for (const root of roots) {
    for (const file of await collectSkillFiles(root)) {
      let content;
      try {
        content = await fs.readFile(file, "utf8");
      } catch {
        continue;
      }
      const frontmatter = parseFrontmatter(content);
      const name = frontmatter?.name;
      if (!name || !SKILL_NAME_RE.test(name) || skills.has(name)) continue;
      skills.set(name, {
        name,
        description: frontmatter.description ?? "",
        sha256: crypto.createHash("sha256").update(content).digest("hex"),
        path: file,
        content,
      });
    }
  }
  return [...skills.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function snapshotFingerprint(skills) {
  const hash = crypto.createHash("sha256");
  for (const skill of skills) hash.update(`${skill.name}:${skill.sha256}\n`);
  return hash.digest("hex");
}

module.exports = { scanDeviceSkills, snapshotFingerprint, parseFrontmatter };
