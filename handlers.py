"""Tool handlers. They never raise: every outcome is a JSON string the model can act on."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from .engine import ShaderError, Store, check, clean_title

logger = logging.getLogger(__name__)

GPU_WAIT_S = 6.0

INSTRUCTION = ("The shader is saved and will animate in the chat. End your reply with exactly "
               "this line, on a line of its own with a blank line before it (not in code or "
               "quotes): ")


def directive(shader_id: str) -> str:
    """The transcript directive the Desktop half draws as a live card."""
    return f'::vitrail{{id="{shader_id}"}}'


def _error(message: str, **extra) -> str:
    return json.dumps({"success": False, "error": message, **extra}, ensure_ascii=False)


def _ask_desktop(shader_id: str, code: str) -> bool:
    """Ask the Desktop half to compile the shader. False when nobody can hear."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event

        broadcast_plugin_event("vitrail", "compile", {"id": shader_id, "code": code})
        return True
    except Exception:  # an older Hermes, or no event stream in this process
        logger.debug("vitrail: compile request not sent", exc_info=True)
        return False


def make_handlers(data_root: Callable[[], Path]) -> dict:
    """`data_root` is called on every use, so the store follows the active profile."""

    def render(args, **kwargs) -> str:
        try:
            args = args if isinstance(args, dict) else {}
            try:
                checked = check(args.get("code"))
            except ShaderError as e:
                return _error(str(e))
            store = Store(data_root())
            title = clean_title(args.get("title"))
            saved = store.save(title, checked, str(args.get("description") or ""))
            payload = {
                "success": True,
                "id": saved["id"],
                "title": title,
                "controls": [c["name"] for c in checked["controls"]],
                "file": saved["html"],
            }
            if checked["warnings"]:
                payload["warnings"] = checked["warnings"]

            report = None
            caps = store.desktop_caps()
            asked = caps is not None and _ask_desktop(saved["id"], checked["code"])
            if asked:
                report = store.wait_report(saved["id"], GPU_WAIT_S)
            if report is None:
                why = ("Hermes Desktop did not answer in time" if asked
                       else "no Hermes Desktop window is listening")
                payload["gpu"] = (f"not verified: {why}. The shader compiles when the user "
                                  "opens it; if it fails in Hermes Desktop, the card offers to "
                                  "send you the error.")
            elif not report.get("ok"):
                return _error(
                    "The shader did not compile on the user's GPU. Fix the source and call "
                    "vitrail_render again with the full corrected code.",
                    log=report.get("log", ""),
                    hint="Line numbers in the log are those of your source.")
            else:
                payload["gpu"] = {k: report[k] for k in ("compile_ms", "frame_ms", "renderer")
                                  if k in report}
                payload["gpu"]["compiled"] = True
                frame_ms = report.get("frame_ms")
                if isinstance(frame_ms, (int, float)) and frame_ms > 40:
                    payload["gpu"]["note"] = (
                        f"Slow: {frame_ms:.0f} ms per frame at preview size. Consider fewer "
                        "loop steps or octaves.")
                poster = store.get(saved["id"])
                if poster and poster.get("poster"):
                    payload["still"] = poster["poster"]
                    payload["still_note"] = ("A frame of the shader at t = 2 s. Look at it with "
                                             "your vision tool if you want to check the result.")
            # A Desktop without tool media renderers draws the card from a directive; every
            # other surface gets the standalone player as a MEDIA file.
            if caps and caps["directive"] and not caps["tool_media"]:
                payload["deliver"] = directive(saved["id"])
            else:
                payload["deliver"] = f"MEDIA:{saved['html']}"
            payload["instruction"] = INSTRUCTION + payload["deliver"]
            return json.dumps(payload, ensure_ascii=False)
        except Exception as e:
            logger.exception("vitrail_render failed")
            return _error(f"Vitrail could not save the shader ({type(e).__name__}: {e}).")

    def library(args, **kwargs) -> str:
        try:
            args = args if isinstance(args, dict) else {}
            store = Store(data_root())
            shader_id = args.get("id")
            if shader_id:
                found = store.get(str(shader_id).strip())
                if found is None:
                    return _error("No shader with that id. Call vitrail_library without an id "
                                  "to list them.")
                return json.dumps({
                    "success": True,
                    "id": found["id"],
                    "title": found.get("title", ""),
                    "description": found.get("description", ""),
                    "code": found["code"],
                    "controls": found.get("controls", []),
                    "gpu": found.get("report") or "not verified",
                    "file": found["html"],
                }, ensure_ascii=False)
            try:
                limit = int(args.get("limit") or 20)
            except (TypeError, ValueError):
                limit = 20
            rows = store.list(limit)
            return json.dumps({"success": True, "count": len(rows), "shaders": [
                {"id": r["id"], "title": r["title"], "description": r["description"],
                 "compiled": r["verified"]} for r in rows]}, ensure_ascii=False)
        except Exception as e:
            logger.exception("vitrail_library failed")
            return _error(f"Vitrail could not read the library ({type(e).__name__}: {e}).")

    return {"vitrail_render": render, "vitrail_library": library}
