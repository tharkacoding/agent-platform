from ..core.sdk import tool
from ..skills import loader


@tool("skill_read", "Read the full instructions (SKILL.md) of a skill and list its resource files.",
      {"skill_id": {"type": "string"}})
def skill_read(ctx, skill_id: str):
    s = loader.get(skill_id)
    if not s:
        return {"error": f"unknown skill {skill_id}"}
    return {"id": s["id"], "name": s["name"], "body": s["body"], "resources": s["resources"]}
