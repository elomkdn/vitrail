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

Expose 2 to 5 parameters that alter distinct, perceptible aspects of the image (for example, sun
height, wave amplitude, pane count, or glow intensity). Every annotated uniform must be used by the
shader; a uniform without an annotation stays at zero. Avoid several controls that all merely
change overall brightness.

Choose ranges and defaults deliberately: the default should already look finished, and both ends
of a slider should remain visually intentional. Use a step of `1` for discrete counts. Before
finishing, check each control at its minimum, default, and maximum (in the live preview when
available); confirm it changes the property its label promises without breaking the composition.

## Compose the image before adding detail

Before writing GLSL, decide in a sentence what the viewer should notice first, what supports it,
and what mood the palette and motion should create. Build that hierarchy with simple shapes and
light before adding noise, particles, or fine texture.

- Give the image one clear focal subject or event. Make its silhouette and scale readable at
  thumbnail size; reserve the strongest contrast and brightest highlights for it.
- Where the scene benefits from it, separate foreground, subject, and background through scale,
  contrast, saturation, softness, or parallax. Let negative space frame the subject instead of
  filling every pixel with detail.
- Make the palette specific to the prompt. A small, related palette with a deliberate warm/cool
  relationship usually reads more clearly than many equally bright colors. Avoid defaulting to
  neon blue-purple gradients, a centered glow, or symmetry unless the subject calls for them.
- Animate a cause, not just a color value: water swells, light drifts, petals open, embers rise.
  Use one legible primary motion and quieter secondary motion; avoid pulsing the whole image or
  layering unrelated sine waves and noise until the focal point disappears.
- Keep procedural detail subordinate to the subject. Noise should suggest material or atmosphere,
  not become visual static. Prefer a few deliberate shapes, edges, reflections, or particles.

Do a quick quality pass before the final render: squint at the image (or inspect the still) and
check that the focal point, depth, and motion remain clear. If it looks generic, change a
composition or material choice, not just the palette. Keep the shader within the performance
limits below.

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
- End the reply with the `deliver` line from the result, on a line of its own after a blank line. Do not paste the
  GLSL into the reply: the card has a copy button. Say in a sentence what the controls do.
- To change a shader, send the whole revised source to `vitrail_render` again.
  `vitrail_library` returns the source of any earlier shader by id.
