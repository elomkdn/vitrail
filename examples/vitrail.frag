// Vitrail: a stained-glass window with light moving behind it.
uniform float cells;   // @slider 2 12 5.5
uniform float lead;    // @slider 0.01 0.2 0.07
uniform float light;   // @slider 0 2 1.1
uniform float speed;   // @slider 0 2 0.5
uniform vec3 warmth;   // @color #ffb45e
uniform bool rose;     // @toggle on

vec2 hash2(vec2 p) {
  p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
  return fract(sin(p) * 43758.5453);
}

vec3 palette(float t) {
  return 0.5 + 0.5 * cos(6.28318 * (t + vec3(0.0, 0.33, 0.67)));
}

// Voronoi: x = distance to the nearest edge, y = id of the cell.
vec2 voronoi(vec2 p) {
  vec2 n = floor(p), f = fract(p);
  vec2 nearest = vec2(0.0), cell = vec2(0.0);
  float best = 8.0;
  for (int j = -1; j <= 1; j++) {
    for (int i = -1; i <= 1; i++) {
      vec2 g = vec2(float(i), float(j));
      vec2 r = g + hash2(n + g) - f;
      float d = dot(r, r);
      if (d < best) { best = d; nearest = r; cell = n + g; }
    }
  }
  float edge = 8.0;
  for (int j = -2; j <= 2; j++) {
    for (int i = -2; i <= 2; i++) {
      vec2 g = vec2(float(i), float(j));
      vec2 r = g + hash2(n + g) - f;
      if (dot(nearest - r, nearest - r) > 0.00001) {
        edge = min(edge, dot(0.5 * (nearest + r), normalize(r - nearest)));
      }
    }
  }
  return vec2(edge, hash2(cell).x);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = (2.0 * fragCoord - iResolution.xy) / iResolution.y;
  float t = iTime * speed;

  vec2 p = uv * cells * 0.5;
  if (rose) {
    // Fold the plane into petals around the centre, like a rose window.
    float a = atan(uv.y, uv.x), r = length(uv);
    a = abs(mod(a, 0.5236) - 0.2618);
    p = vec2(cos(a), sin(a)) * r * cells * 0.5 + vec2(3.0, 7.0);
  }
  vec2 v = voronoi(p);

  // Glass: each pane has its own hue, with ripples in its thickness.
  vec3 glass = palette(v.y + 0.08 * sin(t * 0.3));
  glass = mix(glass, glass * glass, 0.35);
  float ripple = 0.5 + 0.5 * sin(p.x * 9.0 + sin(p.y * 7.0 + v.y * 40.0));
  glass *= 0.82 + 0.18 * ripple;

  // Light behind the window: a slow sun and drifting beams.
  vec2 sun = 0.55 * vec2(cos(t * 0.4), sin(t * 0.27));
  float behind = 0.35 + 0.9 * exp(-2.2 * dot(uv - sun, uv - sun));
  behind += 0.18 * sin(uv.x * 3.0 - uv.y * 2.0 + t);
  vec3 col = glass * warmth * behind * light * 1.6;

  // Lead came between the panes, with a soft halo where the light bleeds.
  float came = smoothstep(lead, lead * 0.45, v.x);
  col += 0.25 * warmth * behind * smoothstep(lead * 3.0, lead, v.x) * (1.0 - came);
  col = mix(col, vec3(0.012, 0.012, 0.016), came);

  if (rose) { col *= smoothstep(1.02, 0.98, length(uv)); }
  col *= 1.0 - 0.25 * dot(uv, uv);
  fragColor = vec4(pow(max(col, 0.0), vec3(0.4545)), 1.0);
}
