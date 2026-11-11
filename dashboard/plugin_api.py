"""Vitrail backend for the Desktop half. Mounted by Hermes at ``/api/plugins/vitrail/``.

Hermes loads this file standalone (not as part of the plugin package), so the engine is loaded
by path under a private name.

Routes:
    POST   /hello            the Desktop half is open (lets vitrail_render wait for the GPU)
    POST   /report           what the GPU said about one shader, with an optional JPEG still
    GET    /shaders          the library, newest first, with sources
    GET    /shaders/{id}     one shader's title and source (the directive card reads it)
    DELETE /shaders/{id}     remove one shader from the library
    POST   /snapshot         save a PNG the user exported from a card; returns its path
"""

from __future__ import annotations

import base64
import binascii
import importlib.util
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query

router = APIRouter()

_ENGINE = "vitrail_engine_api"
MAX_SNAPSHOT_BYTES = 12 * 1024 * 1024


def _engine():
    if _ENGINE not in sys.modules:
        path = Path(__file__).resolve().parent.parent / "engine.py"
        spec = importlib.util.spec_from_file_location(_ENGINE, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_ENGINE] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(_ENGINE, None)
            raise
    return sys.modules[_ENGINE]


def _store():
    from plugins.plugin_storage import plugin_data_dir  # Hermes only; imported lazily

    return _engine().Store(Path(plugin_data_dir("vitrail")))


def _data_url_bytes(url, prefix: str, limit: int) -> bytes | None:
    if not isinstance(url, str) or not url.startswith(prefix):
        return None
    encoded = url[len(prefix):]
    if len(encoded) > limit * 4 // 3 + 8:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None


@router.post("/hello")
def hello(body: dict | None = Body(None)):
    body = body if isinstance(body, dict) else {}
    _store().desktop_seen({"tool_media": body.get("toolMedia"), "directive": body.get("directive")})
    return {"ok": True}


@router.post("/report")
def report(body: dict = Body(...)):
    engine = _engine()
    store = _store()
    store.desktop_seen()
    poster = _data_url_bytes(body.get("poster"), "data:image/jpeg;base64,", engine.MAX_POSTER_BYTES)
    try:
        saved = store.write_report(str(body.get("id") or ""), body, poster)
    except engine.ShaderError:
        saved = False
    if not saved:
        raise HTTPException(status_code=404, detail="Shader not found.")
    return {"ok": True}


@router.get("/shaders")
def shaders(limit: int = Query(60, ge=1, le=100)):
    return {"shaders": _store().list(limit, with_code=True)}


@router.get("/shaders/{shader_id}")
def shader(shader_id: str):
    found = _store().get(shader_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Shader not found.")
    return {"id": found["id"], "title": found.get("title", ""), "code": found["code"]}


@router.delete("/shaders/{shader_id}")
def delete_shader(shader_id: str):
    engine = _engine()
    try:
        removed = _store().delete(shader_id)
    except engine.ShaderError:
        removed = False
    if not removed:
        raise HTTPException(status_code=404, detail="Shader not found.")
    return {"ok": True}


@router.post("/snapshot")
def snapshot(body: dict = Body(...)):
    engine = _engine()
    store = _store()
    png = _data_url_bytes(body.get("png"), "data:image/png;base64,", MAX_SNAPSHOT_BYTES)
    if png is None or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(status_code=400, detail="Expected a PNG data URL.")
    try:
        folder = store.dir(str(body.get("id") or ""))
    except engine.ShaderError:
        raise HTTPException(status_code=404, detail="Shader not found.")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Shader not found.")
    path = folder / f"{folder.name}-{time.strftime('%Y%m%d-%H%M%S')}.png"
    path.write_bytes(png)
    return {"ok": True, "path": str(path)}
