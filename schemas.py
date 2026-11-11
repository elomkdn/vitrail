"""Tool schemas: what the model reads to decide when and how to call the Vitrail tools."""

from __future__ import annotations

CONTRACT = (
    "Write GLSL ES 3.00 in the Shadertoy form: define "
    "`void mainImage(out vec4 fragColor, in vec2 fragCoord)` and nothing else at the top level "
    "except your own functions, constants and control uniforms. Do NOT write `#version`, "
    "`precision`, `void main()` or the built-in uniforms: Vitrail adds them. Built-ins: "
    "`vec3 iResolution` (pixels), `float iTime` (seconds), `float iTimeDelta`, `int iFrame`, "
    "`vec4 iMouse` (xy = pointer while pressed, zw = click position). The image is procedural: "
    "no textures, no iChannel inputs. Keep loops bounded by constants (raymarching: 64 to 100 "
    "steps) so it runs on a laptop GPU."
)

CONTROLS = (
    "Give the user live controls by annotating uniforms, one per line: "
    "`uniform float speed; // @slider 0 2 0.5` (min max default, optional step), "
    "`uniform vec3 tint; // @color #ff8800`, `uniform bool rings; // @toggle on`. Each becomes "
    "a slider, colour picker or switch under the shader, so the user tunes the look without "
    "another prompt. Expose the 2 to 5 parameters that matter most (at most 12)."
)

RENDER = {
    "name": "vitrail_render",
    "description": (
        "Show the user a live, animated GLSL fragment shader. Use it whenever the user asks for "
        "a shader, a generative or animated visual, a procedural background or texture, a "
        "visual effect, or wants a previous shader changed (send the full revised source: each "
        "call makes a new shader). " + CONTRACT + " " + CONTROLS + " "
        "When Hermes Desktop is open, the shader is compiled on the user's GPU before this call "
        "returns: a compile error comes back as `error` with the driver log (line numbers are "
        "those of your source), so fix the source and call again without asking the user. On "
        "success the result has `deliver`, one line of text: end your reply with exactly that "
        "line, on a line of its own after a blank line (not in code or quotes), and do not "
        "paste the GLSL into the reply. For recipes (noise, palettes, SDF raymarching), load the skill "
        "`vitrail:shader-studio`."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short name for the shader, e.g. 'Lagoon at dusk'.",
            },
            "code": {
                "type": "string",
                "description": "Full GLSL source defining mainImage, with annotated control "
                               "uniforms. Up to 64 KB.",
            },
            "description": {
                "type": "string",
                "description": "One sentence on what it shows (kept in the library).",
            },
        },
        "required": ["title", "code"],
    },
}

LIBRARY = {
    "name": "vitrail_library",
    "description": (
        "The user's saved Vitrail shaders. Without `id`: the most recent shaders (id, title, "
        "description). With `id`: that shader's GLSL source, its controls and what the GPU "
        "reported (compiled or not, frame time), so you can explain it, remix it or build on it "
        "with vitrail_render."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "A shader id from vitrail_render or the list."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20,
                      "description": "How many recent shaders to list."},
        },
        "required": [],
    },
}

ALL = (RENDER, LIBRARY)
