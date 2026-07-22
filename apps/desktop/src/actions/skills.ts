import { fetchSkillContent, listSkills } from "@/api/skills";
import { getState } from "@/stores";

export async function fetchSkills(): Promise<void> {
  const s = getState();
  try {
    const skills = await listSkills(s.config);
    s.setSkills(skills);
  } catch {
    /* skills are optional — don't surface an error */
  }
}

/** Fetch a skill's source markdown and pop the in-app viewer. Falls back to
 *  opening the file in the OS default app if the fetch fails (e.g. server
 *  is offline but the file exists locally). */
export async function viewSkill(name: string): Promise<void> {
  const s = getState();
  const skill = s.skills.find((sk) => sk.name === name);
  try {
    const data = await fetchSkillContent(s.config, name);
    s.setViewingMarkdown({
      title: skill?.name ?? data.name,
      subtitle: data.path,
      content: data.content,
      sourcePath: data.path,
    });
  } catch (error) {
    // Couldn't load via server. As a last resort, open externally if we
    // know the path locally.
    if (skill?.path) void window.ardenDesktop?.shell?.openPath(skill.path);
    else {
      s.appendMessage({
        id: crypto.randomUUID(),
        role: "error",
        content: error instanceof Error ? error.message : String(error),
      });
    }
  }
}
