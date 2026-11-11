---
name: shader-studio
description: Recipes and rules for writing GLSL fragment shaders with vitrail_render - noise, palettes, SDF raymarching, live controls, and how to iterate on a look.
---

# Shader studio

`vitrail_render` shows the user a live fragment shader inside the chat. This skill is the
craft: how to write one that compiles first time, looks good and is pleasant to tune.

## The contract

- GLSL ES 3.00, Shadertoy form. Define `void mainImage(out vec4 fragColor, in vec2 fragCoord)`.
- Do not write `#version`, `precision`, `void main()` or the built-in uniforms. Vitrail adds them.
- Built-ins: `vec3 iResolution` (pixels), `float iTime` (seconds), `float iTimeDelta`,
  `int iFrame`, `vec4 iMouse` (xy while pressed, zw where the press started).
- Procedural only: no textures, no `iChannel`, no samplers.
- Loops need constant bounds. Raymarching: 64 to 100 steps. fbm: 4 to 6 octaves.
- Float literals need a decimal point (`1.0`, not `1`). No implicit int to float conversion.
- Alpha is ignored: the frame is opaque.

## Start from centred, aspect-correct coordinates

```glsl
vec2 uv = (2.0 * fragCoord - iResolution.xy) / iResolution.y;   // y in [-1, 1]
```

## Controls: let the user tune it

Annotate a uniform and it becomes a control under the shader. The user moves it and the image
changes at once, with no new prompt.

```glsl
uniform float speed;   // @slider 0 2 0.6        min max default [step]
uniform float detail;  // @slider 1 8 5 1        a step of 1 gives whole numbers
uniform vec3 tint;     // @color #ffb060
uniform bool grain;    // @toggle on
```

Expose the 2 to 5 parameters that change the character of the image (speed, scale, palette
colour, density). Use `bool` toggles in `if (grain) { ... }`. A uniform without an annotation
stays at zero.

## Recipes

Hash and value noise:

```glsl
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = m * p; a *= 0.5; }
  return v;
}
```

Cosine palette (Inigo Quilez):

```glsl
vec3 palette(float t, vec3 a, vec3 b, vec3 c, vec3 d) { return a + b * cos(6.28318 * (c * t + d)); }
// sunset: palette(t, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.0, 0.1, 0.2))
```

Domain warping, for smoke, marble and water:

```glsl
vec2 q = vec2(fbm(p + iTime * 0.1), fbm(p + vec2(5.2, 1.3)));
float v = fbm(p + 3.0 * q);
```

SDF raymarching skeleton:

```glsl
float map(vec3 p) { return length(p) - 1.0; }
vec3 normal(vec3 p) {
  vec2 e = vec2(0.001, 0.0);
  return normalize(vec3(map(p + e.xyy) - map(p - e.xyy),
                        map(p + e.yxy) - map(p - e.yxy),
                        map(p + e.yyx) - map(p - e.yyx)));
}
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = (2.0 * fragCoord - iResolution.xy) / iResolution.y;
  vec3 ro = vec3(0.0, 0.0, 3.0), rd = normalize(vec3(uv, -1.5));
  float t = 0.0;
  for (int i = 0; i < 80; i++) {
    float d = map(ro + rd * t);
    if (d < 0.001 || t > 20.0) break;
    t += d;
  }
  vec3 col = vec3(0.02);
  if (t < 20.0) {
    vec3 n = normal(ro + rd * t);
    col = vec3(0.9) * max(dot(n, normalize(vec3(0.6, 0.7, 0.5))), 0.0);
  }
  fragColor = vec4(pow(col, vec3(0.4545)), 1.0);
}
```

Finish the image: gamma (`pow(col, vec3(0.4545))`), a soft vignette
(`col *= 1.0 - 0.3 * dot(uv, uv)`), and a little grain hide banding.

## Working with the result

- Hermes Desktop compiles the shader on the user's GPU before `vitrail_render` returns. On
  `error`, read the driver log (line numbers are those of your source), fix the source and call
  the tool again in the same turn. Do not show the user a shader that failed.
- `gpu.frame_ms` above 40 means it is too heavy: cut loop steps or octaves.
- `still` is a frame at t = 2 s. If the look matters and you have a vision tool, look at it
  before you answer.
- End the reply with the `MEDIA:` line from the result, on a line of its own. Do not paste the
  GLSL into the reply: the card has a copy button. Say in a sentence what the controls do.
- To change a shader, send the whole revised source to `vitrail_render` again.
  `vitrail_library` returns the source of any earlier shader by id.
