from fastapi import APIRouter, Depends, HTTPException, Request

from arden.constants import SKILL_ARCHIVE_MAX_BYTES
from arden.server.deps import require_skill_service
from arden.server.schemas import InstallRequest
from arden.skills.service import SkillService

router = APIRouter(tags=["skills"])


@router.get("/skills")
async def list_skills(svc: SkillService = Depends(require_skill_service)):
    return {
        "skills": [
            {
                "name": m.name,
                "description": m.description,
                "location": m.location,
                "path": str(m.path / "SKILL.md"),
            }
            for m in svc.list_all()
        ],
    }


@router.get("/skills/governance")
async def skill_governance(svc: SkillService = Depends(require_skill_service)):
    return svc.governance_report()


@router.get("/skills/{name}/content")
async def get_skill_content(name: str, svc: SkillService = Depends(require_skill_service)):
    meta = svc.get(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    skill_md = meta.path / "SKILL.md"
    try:
        content = skill_md.read_text()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read skill: {e}")
    return {
        "name": name,
        "description": meta.description,
        "path": str(skill_md),
        "content": content,
    }


@router.post("/skills/install")
async def install_skill(request: InstallRequest, svc: SkillService = Depends(require_skill_service)):
    try:
        meta = await svc.install(request.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "name": meta.name if meta else request.source,
        "description": meta.description if meta else "",
        "status": "installed",
    }


@router.post("/skills/upload")
async def upload_skill(request: Request, svc: SkillService = Depends(require_skill_service)):
    """Install one skill from a zipped skill directory (raw request body).

    The device-side path for user-authored skills when the server runs
    remotely: zip the skill folder, POST it here.
    """
    data = await request.body()
    if len(data) > SKILL_ARCHIVE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Skill archive too large.")
    try:
        meta = svc.install_archive(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": meta.name, "description": meta.description, "status": "installed"}


@router.delete("/skills/{name}")
async def remove_skill(name: str, svc: SkillService = Depends(require_skill_service)):
    if not svc.remove(name):
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return {"status": "removed", "name": name}
