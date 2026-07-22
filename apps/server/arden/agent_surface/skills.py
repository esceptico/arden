from pathlib import Path


def get_agent_skills_dir(root: Path | str = ".") -> tuple[Path, str]:
    return Path(root) / "agent" / "skills", "agent"
