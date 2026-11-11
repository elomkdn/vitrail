"""Vitrail engine: everything that does not need Hermes.

Checks a Shadertoy-style fragment shader, reads its control annotations, and keeps each shader in
its own folder with a standalone HTML player. Pure standard library, no GPU: the real compile
happens in Hermes Desktop (or in the browser that opens the HTML file).
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from pathlib import Path

MAX_CODE_BYTES = 64 * 1024
MAX_TITLE = 80
MAX_CONTROLS = 12
MAX_POSTER_BYTES = 600 * 1024

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}-[0-9a-f]{6}$")

BUILTINS = ("iResolution", "iTime", "iTimeDelta", "iFrame", "iMouse")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_VERSION = re.compile(r"(?m)^[ \t]*#[ \t]*version\b[^\n]*")
_BUILTIN_DECL = re.compile(
    r"(?m)^[ \t]*uniform[ \t]+(?:(?:lowp|mediump|highp)[ \t]+)?\w+[ \t]+(?:%s)[ \t]*;[^\n]*"
    % "|".join(BUILTINS))
_UNIFORM = re.compile(
    r"(?m)^[ \t]*uniform[ \t]+(?:(?:lowp|mediump|highp)[ \t]+)?(\w+)[ \t]+(\w+)[ \t]*;"
    r"[ \t]*(?://[ \t]*([^\n]*))?$")
_MAIN_IMAGE = re.compile(r"\bvoid\s+mainImage\s*\(")
_MAIN = re.compile(r"\bvoid\s+main\s*\(")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


class ShaderError(ValueError):
    """The shader cannot be accepted as written; the message tells the model what to change."""


def strip_comments(code: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub(" ", code))


def normalize(code: str) -> str:
    """Blank what the player adds itself: the #version line and the built-in uniforms.

    The lines stay (empty), so the line numbers of a compile error are those of the source the
    model wrote.
    """
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    code = _VERSION.sub("", code)
    code = _BUILTIN_DECL.sub("", code)
    return code.rstrip() + "\n"


def _num(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(text)
    return value


def _label(name: str) -> str:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ",
                   re.sub(r"^u_", "", name).replace("_", " ")).strip()
    return (words[:1].upper() + words[1:]) if words else name


def parse_controls(code: str) -> tuple[list[dict], list[str]]:
    """Controls declared as annotated uniforms, plus warnings for the ones left without one.

    uniform float speed; // @slider 0 2 0.5        min max default [step]
    uniform vec3 tint;   // @color #ff8800
    uniform bool rings;  // @toggle on
    """
    controls: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for gl_type, name, note in _UNIFORM.findall(_BLOCK_COMMENT.sub(" ", code)):
        if name in BUILTINS or name in seen:
            continue
        seen.add(name)
        parts = (note or "").split()
        kind = parts[0] if parts else ""
        try:
            if gl_type == "float" and kind == "@slider" and len(parts) >= 4:
                low, high, value = _num(parts[1]), _num(parts[2]), _num(parts[3])
                if high <= low:
                    raise ValueError("max must be above min")
                step = _num(parts[4]) if len(parts) >= 5 else 0.0
                control = {"name": name, "label": _label(name), "kind": "slider", "min": low,
                           "max": high, "value": min(max(value, low), high),
                           "step": step if step > 0 else (high - low) / 200}
            elif gl_type == "vec3" and kind == "@color" and len(parts) >= 2 and _HEX.match(parts[1]):
                control = {"name": name, "label": _label(name), "kind": "color",
                           "value": "#" + _HEX.match(parts[1]).group(1).lower()}
            elif gl_type == "bool" and kind == "@toggle":
                state = parts[1].lower() if len(parts) >= 2 else "off"
                control = {"name": name, "label": _label(name), "kind": "toggle",
                           "value": state in ("on", "true", "1")}
            else:
                raise ValueError("no matching annotation")
        except ValueError:
            warnings.append(
                f"uniform {gl_type} {name} has no valid control annotation and stays at zero. Use "
                "`uniform float x; // @slider min max default`, `uniform vec3 c; // @color #rrggbb` "
                "or `uniform bool b; // @toggle on`.")
            continue
        if len(controls) < MAX_CONTROLS:
            controls.append(control)
        else:
            warnings.append(f"more than {MAX_CONTROLS} controls: {name} is ignored.")
    return controls, warnings


def _balance(bare: str) -> str | None:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in bare:
        if ch == "\n":
            line += 1
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in pairs:
            if not stack or stack[-1][0] != pairs[ch]:
                return f"unexpected `{ch}` on line {line}"
            stack.pop()
    if stack:
        ch, at = stack[-1]
        return f"`{ch}` opened on line {at} is never closed"
    return None


def check(code) -> dict:
    """Normalise and statically check a shader. Returns {code, controls, warnings} or raises."""
    if not isinstance(code, str) or not code.strip():
        raise ShaderError("`code` is empty. Send the full GLSL source of the fragment shader.")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise ShaderError(f"The shader is larger than {MAX_CODE_BYTES // 1024} KB. Simplify it.")
    code = normalize(code)
    bare = strip_comments(code)
    if not _MAIN_IMAGE.search(bare):
        if _MAIN.search(bare):
            raise ShaderError(
                "Write the Shadertoy entry point instead of main(): "
                "`void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Vitrail adds main(), "
                "the #version line, the precision and the built-in uniforms itself.")
        raise ShaderError(
            "The shader has no entry point. Define "
            "`void mainImage(out vec4 fragColor, in vec2 fragCoord)`.")
    if _MAIN.search(bare):
        raise ShaderError("Remove `void main()`: Vitrail adds it and calls mainImage().")
    if re.search(r"\bgl_FragColor\b", bare):
        raise ShaderError("gl_FragColor does not exist in GLSL ES 3.00. Assign to `fragColor`, "
                          "the out parameter of mainImage.")
    if re.search(r"\biChannel\d\b|\biChannelResolution\b|\bsampler(?:2D|3D|Cube)\b", bare):
        raise ShaderError("Vitrail shaders are procedural: there are no textures, iChannel inputs "
                          "or samplers. Build the image from math (noise, SDFs, palettes).")
    problem = _balance(bare)
    if problem:
        raise ShaderError(f"Unbalanced brackets: {problem}.")
    controls, warnings = parse_controls(code)
    if re.search(r"\btexture2D\s*\(", bare):
        warnings.append("texture2D() is GLSL ES 1.00; it will not compile here.")
    return {"code": code, "controls": controls, "warnings": warnings}


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return slug or "shader"


def clean_title(title) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    return text[:MAX_TITLE] or "Untitled shader"


# ── Standalone player ───────────────────────────────────────────────────────

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · Vitrail</title>
<style>
  html, body { margin: 0; height: 100%; background: #0b0b0f; color: #e8e6e1;
    font: 13px/1.4 system-ui, sans-serif; overflow: hidden; }
  canvas { display: block; width: 100vw; height: 100vh; }
  #panel { position: fixed; left: 12px; bottom: 12px; max-width: min(320px, calc(100vw - 24px));
    padding: 10px 12px; border-radius: 10px; background: rgba(11, 11, 15, .72);
    backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, .12); }
  #panel h1 { margin: 0 0 6px; font-size: 13px; font-weight: 600; }
  #panel label { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; margin-top: 6px; }
  #panel input[type=range] { grid-column: 1 / -1; width: 100%; }
  #panel output { opacity: .7; font-variant-numeric: tabular-nums; }
  #error { position: fixed; inset: 0; margin: 0; padding: 24px; white-space: pre-wrap;
    font: 12px/1.5 ui-monospace, monospace; color: #ffb4a8; background: #0b0b0f; overflow: auto; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<canvas id="view"></canvas>
<pre id="error" hidden></pre>
<div id="panel"><h1>__TITLE__</h1><div id="controls"></div></div>
<script>
const SHADER = __SHADER__;
const CONTROLS = __CONTROLS__;
const HEAD = `#version 300 es
precision highp float;
uniform vec3 iResolution; uniform float iTime; uniform float iTimeDelta;
uniform int iFrame; uniform vec4 iMouse;
out vec4 vitrailColor;
`;
const TAIL = `
void main() { vec4 c = vec4(0.0, 0.0, 0.0, 1.0); mainImage(c, gl_FragCoord.xy);
  vitrailColor = vec4(c.rgb, 1.0); }
`;
const canvas = document.getElementById('view');
const errorBox = document.getElementById('error');
const fail = text => { errorBox.textContent = text; errorBox.hidden = false; };
const gl = canvas.getContext('webgl2', { antialias: false, alpha: false });
function build() {
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, '#version 300 es\nin vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }');
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, HEAD + SHADER + TAIL);
  gl.compileShader(fs);
  if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS)) {
    fail('This shader did not compile on this GPU.\n\n' + gl.getShaderInfoLog(fs));
    return null;
  }
  const program = gl.createProgram();
  gl.attachShader(program, vs); gl.attachShader(program, fs);
  gl.bindAttribLocation(program, 0, 'p');
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    fail('This shader did not link.\n\n' + gl.getProgramInfoLog(program));
    return null;
  }
  return program;
}
const program = gl ? build() : (fail('WebGL 2 is not available in this browser.'), null);
if (program) {
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.useProgram(program);
  const at = name => gl.getUniformLocation(program, name);
  const values = {};
  const host = document.getElementById('controls');
  for (const c of CONTROLS) {
    values[c.name] = c.value;
    const label = document.createElement('label');
    const name = document.createElement('span');
    name.textContent = c.label;
    label.append(name);
    const input = document.createElement('input');
    if (c.kind === 'slider') {
      const out = document.createElement('output');
      out.textContent = Number(c.value).toFixed(2);
      label.append(out);
      Object.assign(input, { type: 'range', min: c.min, max: c.max, step: c.step, value: c.value });
      input.oninput = () => { values[c.name] = Number(input.value); out.textContent = Number(input.value).toFixed(2); };
    } else if (c.kind === 'color') {
      Object.assign(input, { type: 'color', value: c.value });
      input.oninput = () => { values[c.name] = input.value; };
    } else {
      Object.assign(input, { type: 'checkbox', checked: c.value });
      input.oninput = () => { values[c.name] = input.checked; };
    }
    label.append(input);
    host.append(label);
  }
  const mouse = [0, 0, 0, 0];
  const point = e => {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) * canvas.width / r.width, (r.bottom - e.clientY) * canvas.height / r.height];
  };
  canvas.onpointerdown = e => { const [x, y] = point(e); mouse[0] = mouse[2] = x; mouse[1] = mouse[3] = y; canvas.setPointerCapture(e.pointerId); };
  canvas.onpointermove = e => { if (e.buttons) { const [x, y] = point(e); mouse[0] = x; mouse[1] = y; } };
  canvas.onpointerup = () => { mouse[2] = -Math.abs(mouse[2]); mouse[3] = -Math.abs(mouse[3]); };
  let frame = 0, last = 0;
  const start = performance.now();
  function draw(now) {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const h = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
    const t = (now - start) / 1000;
    gl.uniform3f(at('iResolution'), w, h, 1);
    gl.uniform1f(at('iTime'), t);
    gl.uniform1f(at('iTimeDelta'), t - last);
    gl.uniform1i(at('iFrame'), frame++);
    gl.uniform4f(at('iMouse'), mouse[0], mouse[1], mouse[2], mouse[3]);
    for (const c of CONTROLS) {
      const v = values[c.name];
      if (c.kind === 'slider') gl.uniform1f(at(c.name), v);
      else if (c.kind === 'toggle') gl.uniform1i(at(c.name), v ? 1 : 0);
      else gl.uniform3f(at(c.name), parseInt(v.slice(1, 3), 16) / 255, parseInt(v.slice(3, 5), 16) / 255, parseInt(v.slice(5, 7), 16) / 255);
    }
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    last = t;
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}
</script>
</body>
</html>
"""


def _js_literal(value) -> str:
    """JSON that is safe inside an inline script: no `<` at all, no line separators."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _html_text(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def standalone_html(title: str, code: str, controls: list[dict]) -> str:
    return (_PAGE.replace("__TITLE__", _html_text(title))
            .replace("__SHADER__", _js_literal(code))
            .replace("__CONTROLS__", _js_literal(controls)))


# ── Store ───────────────────────────────────────────────────────────────────

def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Store:
    """One folder per shader under <root>/shaders, plus the Desktop heartbeat."""

    DESKTOP_FRESH_S = 150

    def __init__(self, root: Path):
        self.root = Path(root)
        self.shaders = self.root / "shaders"

    def dir(self, shader_id: str) -> Path:
        if not isinstance(shader_id, str) or not ID_RE.match(shader_id):
            raise ShaderError("Unknown shader id.")
        return self.shaders / shader_id

    def save(self, title: str, checked: dict, description: str = "") -> dict:
        self.shaders.mkdir(parents=True, exist_ok=True)
        for _ in range(8):
            shader_id = f"{slugify(title)}-{secrets.token_hex(3)}"
            folder = self.shaders / shader_id
            try:
                folder.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise ShaderError("Could not allocate a shader id. Try again.")
        meta = {"id": shader_id, "title": title, "description": description[:400],
                "created": time.time(), "controls": checked["controls"]}
        _write(folder / "shader.frag", checked["code"])
        _write(folder / f"{shader_id}.html",
               standalone_html(title, checked["code"], checked["controls"]))
        _write(folder / "meta.json", json.dumps(meta, indent=1))
        return {**meta, "html": str(folder / f"{shader_id}.html"),
                "frag": str(folder / "shader.frag")}

    def get(self, shader_id: str) -> dict | None:
        try:
            folder = self.dir(shader_id)
        except ShaderError:
            return None
        meta = _read_json(folder / "meta.json")
        if meta is None:
            return None
        try:
            code = (folder / "shader.frag").read_text(encoding="utf-8")
        except OSError:
            return None
        report = _read_json(folder / "report.json")
        poster = folder / "poster.jpg"
        return {**meta, "id": shader_id, "code": code, "report": report,
                "html": str(folder / f"{shader_id}.html"),
                "poster": str(poster) if poster.is_file() else None}

    def list(self, limit: int = 30, with_code: bool = False) -> list[dict]:
        if not self.shaders.is_dir():
            return []
        rows = []
        for folder in self.shaders.iterdir():
            if not ID_RE.match(folder.name):
                continue
            meta = _read_json(folder / "meta.json")
            if meta is None:
                continue
            report = _read_json(folder / "report.json")
            if report is not None and report.get("ok") is False:
                continue  # never compiled: not part of the library
            row = {"id": folder.name, "title": meta.get("title", folder.name),
                   "description": meta.get("description", ""),
                   "created": meta.get("created", 0),
                   "verified": bool(report and report.get("ok"))}
            if with_code:
                try:
                    row["code"] = (folder / "shader.frag").read_text(encoding="utf-8")
                except OSError:
                    continue
            rows.append(row)
        rows.sort(key=lambda r: r["created"], reverse=True)
        return rows[:max(1, min(int(limit), 100))]

    def delete(self, shader_id: str) -> bool:
        folder = self.dir(shader_id)
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
        return True

    # The Desktop half reports what the GPU said about a shader.

    def write_report(self, shader_id: str, report: dict, poster: bytes | None = None) -> bool:
        folder = self.dir(shader_id)
        if not folder.is_dir():
            return False
        if poster and len(poster) <= MAX_POSTER_BYTES and poster[:3] == b"\xff\xd8\xff":
            tmp = folder / "poster.jpg.tmp"
            tmp.write_bytes(poster)
            tmp.replace(folder / "poster.jpg")
        log = str(report.get("log") or "")[:4000]
        clean = {"ok": bool(report.get("ok")), "log": log, "at": time.time()}
        for key in ("compile_ms", "frame_ms"):
            value = report.get(key)
            if isinstance(value, (int, float)) and 0 <= value < 1e7:
                clean[key] = round(float(value), 1)
        if isinstance(report.get("renderer"), str):
            clean["renderer"] = report["renderer"][:120]
        _write(folder / "report.json", json.dumps(clean, indent=1))
        return True

    def wait_report(self, shader_id: str, timeout: float) -> dict | None:
        path = self.dir(shader_id) / "report.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            report = _read_json(path)
            if report is not None:
                return report
            time.sleep(0.05)
        return _read_json(path)

    def desktop_seen(self, caps: dict | None = None) -> None:
        """A Desktop window says it is listening, and which transcript surfaces it has."""
        seen = {"at": time.time()}
        previous = _read_json(self.root / "desktop.json") or {}
        for key in ("tool_media", "directive"):
            value = (caps or {}).get(key, previous.get(key))
            if isinstance(value, bool):
                seen[key] = value
        self.root.mkdir(parents=True, exist_ok=True)
        _write(self.root / "desktop.json", json.dumps(seen))

    def desktop_caps(self) -> dict | None:
        """What the listening Desktop window can draw, or None when none is listening."""
        seen = _read_json(self.root / "desktop.json")
        at = seen.get("at") if seen else None
        if not isinstance(at, (int, float)) or not 0 <= time.time() - at < self.DESKTOP_FRESH_S:
            return None
        return {"tool_media": seen.get("tool_media") is True,
                "directive": seen.get("directive") is True}

    def desktop_is_live(self) -> bool:
        return self.desktop_caps() is not None
