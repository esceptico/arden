---
name: add-skill
description: Use this skill when the user wants to create a new skill or remove an existing skill. This includes writing a SKILL.md from scratch and adding scripts/assets.
---

# Add / Remove a Skill

Skills are discovered from three places:
- **Builtin**: shipped with arden, don't touch
- **Project**: `.skills/` under the server working directory
- **Global**: `~/.arden/skills/` — user skills, create here

A skill is a directory containing at minimum a `SKILL.md` file.

## Directory structure

```
~/.arden/skills/my-skill/
├── SKILL.md          # required
├── scripts/          # optional: executable scripts
├── references/       # optional: extra docs loaded on demand
└── assets/           # optional: templates, data files
```

## SKILL.md format

```markdown
---
name: my-skill
description: What it does and WHEN to use it. Be specific — this is what I read to decide whether to activate the skill.
---

# Skill Title

Step-by-step instructions...
```

### Frontmatter rules
- `name`: lowercase, hyphens only, must match directory name, max 64 chars
- `description`: max 1024 chars — include keywords that match user intent
- Both fields are required

## Creating a skill

1. Ask whether the skill should be global (`~/.arden/skills/<name>/`) or project-local (`.skills/<name>/`). Default to global unless the user asks for project-local behavior.
2. `mkdir -p <target>/<name>/scripts`
3. Write `SKILL.md` with frontmatter + instructions
4. Add any scripts/assets/references only when needed — reference them with `<skill_path>/...` in SKILL.md
5. Restart the server after creating a skill manually so the registry rescans skills

## Removing a skill

Delete the directory. Only project/global skills can be removed (not builtins).

## Listing installed skills

```bash
ls ~/.arden/skills/
ls /Users/escept1co/src/arden/skills/
```

## After creating

- Restart the server so the new skill appears in `<available_skills>`
- Test by asking the agent to use the skill or by invoking `use_skill(skill="<name>")`

## Tips

- Keep `SKILL.md` under 500 lines — move heavy reference material to `references/`
- Scripts should be self-contained with clear error messages
- The skill's absolute path is injected as `<skill_path>` at load time — use it to reference sibling files
