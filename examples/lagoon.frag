// Lagoon at dusk: a low sun, layered clouds and its reflection on moving water.
uniform float sunHeight;  // @slider -0.1 0.6 0.16
uniform float swell;      // @slider 0 2 1.0
uniform float haze;       // @slider 0 1 0.45
uniform vec3 sunColor;    // @color #ff8a3c
uniform vec3 skyColor;    // @color #2a3f7a

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

vec3 sky(vec2 uv, vec2 sun) {
  float d = length(uv - sun);
  vec3 col = mix(sunColor * 0.9, skyColor, smoothstep(-0.05, 0.9, uv.y));
  col = mix(col, vec3(0.03, 0.04, 0.10), smoothstep(0.5, 1.4, uv.y));
  col += sunColor * (0.9 * exp(-9.0 * d) + 0.35 * exp(-2.5 * d));
  col += vec3(1.0, 0.95, 0.85) * smoothstep(0.075, 0.068, d);
  float clouds = fbm(vec2(uv.x * 1.6 + iTime * 0.02, uv.y * 5.0));
  float band = smoothstep(0.45, 0.75, clouds) * smoothstep(0.0, 0.25, uv.y) * smoothstep(0.9, 0.3, uv.y);
  col = mix(col, mix(sunColor, vec3(0.10, 0.08, 0.16), smoothstep(0.0, 0.5, uv.y - sun.y)), band * 0.75);
  return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = (2.0 * fragCoord - iResolution.xy) / iResolution.y;
  vec2 sun = vec2(0.25, sunHeight);
  vec3 col;
  if (uv.y > 0.0) {
    col = sky(uv, sun);
  } else {
    // Water: look up the sky mirrored, pushed around by the waves.
    float depth = -uv.y;
    vec2 w = vec2(uv.x / (depth + 0.08), 1.0 / (depth + 0.08));
    float wave = fbm(w * vec2(0.35, 0.6) + vec2(iTime * 0.15, iTime * 0.5)) - 0.5;
    wave += 0.5 * (fbm(w * vec2(1.1, 1.9) - vec2(0.0, iTime * 0.8)) - 0.5);
    vec2 mirrored = vec2(uv.x + wave * swell * 0.22 * (0.25 + depth), depth + wave * swell * 0.10);
    col = sky(mirrored, sun) * mix(0.85, 0.35, smoothstep(0.0, 0.9, depth));
    col += sunColor * 0.5 * pow(max(0.0, 1.0 - abs(mirrored.x - sun.x) * 3.0), 6.0)
         * smoothstep(0.2, 0.9, wave * 2.0 + 0.5) * exp(-2.0 * depth);
    col = mix(col, vec3(0.02, 0.05, 0.09), smoothstep(0.3, 1.1, depth) * 0.6);
  }
  // Haze on the horizon, and a far shore.
  col = mix(col, sunColor * 0.6 + 0.15, haze * exp(-22.0 * abs(uv.y)));
  float shore = 0.022 + 0.018 * noise(vec2(uv.x * 7.0, 1.0)) + 0.03 * smoothstep(0.9, 1.8, abs(uv.x + 0.6));
  col = mix(col, vec3(0.02, 0.025, 0.04), smoothstep(shore, shore - 0.006, abs(uv.y - 0.004)) * step(0.0, uv.y + 0.004));
  col = pow(max(col, 0.0), vec3(0.4545));
  col += (hash(fragCoord * 0.71 + fract(iTime) * 91.0) - 0.5) * 0.012;
  fragColor = vec4(col, 1.0);
}
