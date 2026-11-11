"""Vitrail: live GLSL shaders in the chat, compiled on the user's GPU before the model answers."""

from __future__ import annotations

import logging
from pathlib import Path

from . import hooks, schemas
from .handlers import make_handlers

logger = logging.getLogger(__name__)

TOOLSET = "vitrail"
SKILL_MD = Path(__file__).resolve().parent / "skills" / "shader-studio" / "SKILL.md"


def _data_root() -> Path:
    from plugins.plugin_storage import plugin_data_dir  # resolved per call: follows the profile

    return Path(plugin_data_dir("vitrail"))


def _skill_description(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def register(ctx):
    handlers = make_handlers(_data_root)
    for schema in schemas.ALL:
        name = schema["name"]
        try:
            ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name],
                              emoji="🪟")
        except Exception:
            logger.warning("vitrail: tool %s not registered", name, exc_info=True)
    for event, callback in (("post_tool_call", hooks.record_media),
                            ("transform_llm_output", hooks.append_missing_media)):
        try:
            ctx.register_hook(event, callback)
        except Exception:
            logger.warning("vitrail: hook %s not registered; the instruction in each result "
                           "still asks the model to attach the shader", event, exc_info=True)
    try:
        ctx.register_skill("shader-studio", SKILL_MD, description=_skill_description(SKILL_MD))
    except Exception:
        logger.warning("vitrail: skill shader-studio not registered", exc_info=True)
