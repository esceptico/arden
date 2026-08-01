import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { scanDeviceSkills, snapshotFingerprint } = require("../electron/executor-skills.cjs");

function skillDir(): string {
  return mkdtempSync(join(tmpdir(), "arden-skills-test-"));
}

function writeSkill(root: string, name: string, body = "# Steps\n"): void {
  mkdirSync(join(root, name), { recursive: true });
  writeFileSync(join(root, name, "SKILL.md"), `---\nname: ${name}\ndescription: Skill ${name}\n---\n\n${body}`);
}

describe("device skill scan", () => {
  test("finds skills recursively with frontmatter metadata", async () => {
    const root = skillDir();
    writeSkill(root, "alpha-skill");
    mkdirSync(join(root, "nested"), { recursive: true });
    writeSkill(join(root, "nested"), "beta-skill");

    const skills = await scanDeviceSkills([root]);
    expect(skills.map((s: { name: string }) => s.name)).toEqual(["alpha-skill", "beta-skill"]);
    expect(skills[0].description).toBe("Skill alpha-skill");
    expect(skills[0].sha256).toHaveLength(64);
    expect(skills[0].content).toContain("# Steps");
  });

  test("skips invalid names and files without frontmatter", async () => {
    const root = skillDir();
    writeSkill(root, "good-skill");
    mkdirSync(join(root, "Bad_Name"), { recursive: true });
    writeFileSync(join(root, "Bad_Name", "SKILL.md"), "---\nname: Bad_Name\n---\n\nx");
    mkdirSync(join(root, "no-front"), { recursive: true });
    writeFileSync(join(root, "no-front", "SKILL.md"), "just markdown");

    const skills = await scanDeviceSkills([root]);
    expect(skills.map((s: { name: string }) => s.name)).toEqual(["good-skill"]);
  });

  test("fingerprint changes only when a skill changes", async () => {
    const root = skillDir();
    writeSkill(root, "alpha-skill");
    const first = snapshotFingerprint(await scanDeviceSkills([root]));
    const second = snapshotFingerprint(await scanDeviceSkills([root]));
    expect(second).toBe(first);

    writeSkill(root, "alpha-skill", "# Changed\n");
    const third = snapshotFingerprint(await scanDeviceSkills([root]));
    expect(third).not.toBe(first);
  });

  test("missing root is an empty scan", async () => {
    expect(await scanDeviceSkills(["/definitely/not/here"])).toEqual([]);
  });
});
