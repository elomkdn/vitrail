// Lumignon: one candle flame in the dark. Drag to blow on it.
uniform float flicker;   // @slider 0 2 0.8
uniform float heightK;   // @slider 0.5 1.6 1.0
uniform float glow;      // @slider 0 2 1.0
uniform vec3 flameColor; // @color #ff9a2e

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + 17.0; a *= 0.5; }
  return v;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = (2.0 * fragCoord - iResolution.xy) / iResolution.y;
  float t = iTime;

  // The pointer is a draught: it bends the flame away from itself.
  float wind = 0.0;
  if (iMouse.z > 0.0) {
    vec2 m = (2.0 * iMouse.xy - iResolution.xy) / iResolution.y;
    wind = clamp((0.0 - m.x) * 1.2, -0.8, 0.8);
  }

  vec2 p = uv - vec2(0.0, -0.32);
  float y = p.y / heightK;
  float sway = (fbm(vec2(t * 1.3, 3.0)) - 0.5) * 0.35 * flicker + wind;
  p.x -= sway * y * y * 1.6;
  p.x += (fbm(vec2(p.y * 3.0 - t * 2.2, t * 0.7)) - 0.5) * 0.10 * flicker * smoothstep(0.0, 0.8, y);

  // A teardrop: wide at the wick, pointed at the tip.
  float width = 0.17 * sqrt(max(y, 0.0)) * (1.0 - smoothstep(0.0, 1.0, y)) * 2.2;
  float body = smoothstep(width, width * 0.25, abs(p.x)) * step(0.0, y) * smoothstep(1.0, 0.7, y);
  float core = smoothstep(width * 0.6, 0.0, abs(p.x)) * smoothstep(0.0, 0.12, y) * smoothstep(0.75, 0.2, y);

  vec3 col = vec3(0.0);
  col += flameColor * body * (0.9 + 0.3 * fbm(vec2(t * 4.0, 1.0)) * flicker);
  col += vec3(1.0, 0.93, 0.75) * core * 0.9;
  col += vec3(0.15, 0.3, 1.0) * smoothstep(0.10, 0.0, y) * smoothstep(0.06, 0.0, abs(p.x)) * step(-0.02, y) * 0.8;

  // Halo, wick and the top of the candle.
  float d = length((uv - vec2(sway * 0.12, -0.05)) * vec2(1.0, 0.75));
  float pulse = 0.9 + 0.1 * sin(t * 9.0) * flicker * fbm(vec2(t, 9.0));
  col += flameColor * glow * pulse * (0.35 * exp(-3.2 * d) + 0.10 * exp(-0.9 * d));
  col = mix(col, vec3(0.02), smoothstep(0.008, 0.004, abs(uv.x)) * step(-0.36, uv.y) * step(uv.y, -0.30));
  float wax = smoothstep(0.125, 0.12, abs(uv.x)) * smoothstep(-0.355, -0.36, uv.y);
  vec3 waxCol = mix(vec3(0.95, 0.86, 0.70), flameColor, 0.35) * (0.25 + 0.75 * exp(-5.0 * (-0.36 - uv.y))) * glow;
  col = mix(col, waxCol * (0.75 + 0.25 * smoothstep(0.125, 0.0, abs(uv.x))), wax);

  col = pow(max(col, 0.0), vec3(0.4545));
  col += (hash(fragCoord * 0.71 + fract(t) * 91.0) - 0.5) * 0.012;
  fragColor = vec4(col, 1.0);
}
