"""MEDIA guardrail: a reply always delivers the shader vitrail_render saved during its turn.

post_tool_call remembers the deliver path of each successful vitrail_render; transform_llm_output
appends the MEDIA line when the reply does not already carry it. Records are keyed by
(session_id, turn_id), which Hermes passes to both hooks, so a turn that ends without text cannot
push its file into a later reply.
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict

MEDIA_TOOLS = frozenset({"vitrail_render"})
MAX_TURNS = 50
_FENCED = re.compile(r"(?s)```.*?(?:```|\Z)")

_lock = threading.Lock()
_pending: "OrderedDict[tuple[str, str], list[str]]" = OrderedDict()


def _key(session_id, turn_id):
    key = (str(session_id or ""), str(turn_id or ""))
    return key if any(key) else None


def _tool_text(result) -> str:
    """The JSON text of a tool result: the string itself, or an envelope's first text part."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text", ""))
    return ""


def _deliver_path(result):
    try:
        payload = json.loads(_tool_text(result))
        deliver = payload.get("deliver") if payload.get("success") is True else None
    except (ValueError, AttributeError):
        return None
    if isinstance(deliver, str) and deliver.startswith("MEDIA:") and len(deliver) > 6:
        return deliver[6:]
    return None


def _delivered(path: str, text: str) -> bool:
    """True when MEDIA:<path> stands on a line of its own outside fenced code."""
    visible = _FENCED.sub("", text)
    return re.search(rf"(?m)^[ \t]*MEDIA:{re.escape(path)}[ \t\r]*$", visible) is not None


def record_media(tool_name=None, args=None, result=None, **kw) -> None:
    if tool_name not in MEDIA_TOOLS:
        return None
    key = _key(kw.get("session_id"), kw.get("turn_id"))
    path = _deliver_path(result)
    if key is None or not path:
        return None
    with _lock:
        paths = _pending.pop(key, [])
        if path not in paths:
            paths.append(path)
        _pending[key] = paths
        while len(_pending) > MAX_TURNS:
            _pending.popitem(last=False)
    return None


def append_missing_media(response_text=None, session_id=None, **kw):
    key = _key(session_id, kw.get("turn_id"))
    if key is None:
        return None
    with _lock:
        paths = _pending.pop(key, None)
    if not paths or not isinstance(response_text, str):
        return None
    missing = [p for p in paths if not _delivered(p, response_text)]
    if not missing:
        return None
    return response_text + "".join(f"\n\nMEDIA:{p}" for p in missing)
