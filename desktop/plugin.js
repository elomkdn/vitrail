// Vitrail, the Desktop half: live GLSL shaders in the transcript and a gallery page.
//
// One WebGL 2 context is shared by every shader on screen (browsers cap the number of live
// contexts): each player owns a 2D canvas and the engine blits the frame it rendered into it.
// The same context compiles the shaders vitrail_render asks about, so the model learns what
// the GPU said before it answers.

import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const { host } = sdk

const TOOL = 'vitrail_render'
const ID_RE = /^[a-z0-9][a-z0-9-]{0,47}-[0-9a-f]{6}$/
const BUILTINS = ['iResolution', 'iTime', 'iTimeDelta', 'iFrame', 'iMouse']
const MAX_CONTROLS = 12
const MAX_PROGRAMS = 24
const MAX_SIDE = 2560

// ── Shader source: the same rules as engine.py ──────────────────────────────

const VERSION_LINE = /^[ \t]*#[ \t]*version\b[^\n]*/gm
const BUILTIN_DECL = new RegExp(
  '^[ \\t]*uniform[ \\t]+(?:(?:lowp|mediump|highp)[ \\t]+)?\\w+[ \\t]+(?:' +
    BUILTINS.join('|') +
    ')[ \\t]*;[^\\n]*',
  'gm'
)
const UNIFORM_LINE =
  /^[ \t]*uniform[ \t]+(?:(?:lowp|mediump|highp)[ \t]+)?(\w+)[ \t]+(\w+)[ \t]*;[ \t]*(?:\/\/[ \t]*([^\n]*))?$/gm
const BLOCK_COMMENT = /\/\*[\s\S]*?\*\//g
const HEX = /^#?([0-9a-fA-F]{6})$/

export function normalize(code) {
  return (
    String(code)
      .replace(/\r\n?/g, '\n')
      .replace(VERSION_LINE, '')
      .replace(BUILTIN_DECL, '')
      .trimEnd() + '\n'
  )
}

function labelOf(name) {
  const words = name
    .replace(/^u_/, '')
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])(?=[A-Z])/g, '$1 ')
    .trim()

  return words ? words[0].toUpperCase() + words.slice(1) : name
}

function finite(text) {
  const value = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/.test(text) ? Number(text) : NaN

  if (!Number.isFinite(value)) {
    throw new Error('not a number')
  }

  return value
}

export function parseControls(code) {
  const controls = []
  const seen = new Set()

  for (const match of code.replace(BLOCK_COMMENT, ' ').matchAll(UNIFORM_LINE)) {
    const [, type, name, note] = match

    if (BUILTINS.includes(name) || seen.has(name)) {
      continue
    }

    seen.add(name)
    const parts = (note || '').split(/\s+/).filter(Boolean)
    const kind = parts[0] || ''
    let control = null

    try {
      if (type === 'float' && kind === '@slider' && parts.length >= 4) {
        const min = finite(parts[1])
        const max = finite(parts[2])
        const value = finite(parts[3])

        if (max > min) {
          const step = parts.length >= 5 ? finite(parts[4]) : 0

          control = {
            name,
            label: labelOf(name),
            kind: 'slider',
            min,
            max,
            value: Math.min(Math.max(value, min), max),
            step: step > 0 ? step : (max - min) / 200
          }
        }
      } else if (type === 'vec3' && kind === '@color' && parts.length >= 2 && HEX.test(parts[1])) {
        control = { name, label: labelOf(name), kind: 'color', value: '#' + HEX.exec(parts[1])[1].toLowerCase() }
      } else if (type === 'bool' && kind === '@toggle') {
        const state = (parts[1] || 'off').toLowerCase()

        control = { name, label: labelOf(name), kind: 'toggle', value: ['on', 'true', '1'].includes(state) }
      }
    } catch {
      control = null
    }

    if (control && controls.length < MAX_CONTROLS) {
      controls.push(control)
    }
  }

  return controls
}

function defaultsOf(controls) {
  const values = {}

  for (const control of controls) {
    values[control.name] = control.value
  }

  return values
}

// ── The shared WebGL engine ─────────────────────────────────────────────────

const HEAD = `#version 300 es
precision highp float;
precision highp int;
uniform vec3 iResolution;
uniform float iTime;
uniform float iTimeDelta;
uniform int iFrame;
uniform vec4 iMouse;
out vec4 vitrailColor;
#line 1
`
const TAIL = `
void main() {
  vec4 vitrailOut = vec4(0.0, 0.0, 0.0, 1.0);
  mainImage(vitrailOut, gl_FragCoord.xy);
  vitrailColor = vec4(vitrailOut.rgb, 1.0);
}
`
const VERTEX = `#version 300 es
in vec2 vitrailCorner;
void main() { gl_Position = vec4(vitrailCorner, 0.0, 1.0); }
`

const engine = {
  canvas: null,
  gl: null,
  vertex: null,
  programs: new Map(),
  players: new Set(),
  raf: 0,
  last: 0,
  quality: 1,
  slow: 0,
  scratch: null
}

function glContext() {
  if (engine.gl && !engine.gl.isContextLost()) {
    return engine.gl
  }

  if (engine.gl) {
    return null // lost: wait for the restore event
  }

  const canvas = document.createElement('canvas')

  canvas.width = 640
  canvas.height = 360
  const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, powerPreference: 'high-performance' })

  if (!gl) {
    return null
  }

  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault()
    engine.programs.clear()
    engine.vertex = null
  })
  canvas.addEventListener('webglcontextrestored', () => {
    setupGeometry(gl)
    kick()
  })
  engine.canvas = canvas
  engine.gl = gl
  setupGeometry(gl)

  return gl
}

function setupGeometry(gl) {
  const vao = gl.createVertexArray()

  gl.bindVertexArray(vao)
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
  gl.enableVertexAttribArray(0)
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)

  const vertex = gl.createShader(gl.VERTEX_SHADER)

  gl.shaderSource(vertex, VERTEX)
  gl.compileShader(vertex)
  engine.vertex = vertex
}

/** Compile `source` (already normalised). Cached: { ok, log, ms, program, at(name) }. */
function programFor(source) {
  const cached = engine.programs.get(source)

  if (cached) {
    engine.programs.delete(source)
    engine.programs.set(source, cached) // most recently used last

    return cached
  }

  const gl = glContext()

  if (!gl || !engine.vertex) {
    return { ok: false, log: 'WebGL 2 is not available in this window.', ms: 0, transient: true }
  }

  const started = performance.now()
  const fragment = gl.createShader(gl.FRAGMENT_SHADER)

  gl.shaderSource(fragment, HEAD + source + TAIL)
  gl.compileShader(fragment)
  let entry

  if (!gl.getShaderParameter(fragment, gl.COMPILE_STATUS)) {
    entry = { ok: false, log: cleanLog(gl.getShaderInfoLog(fragment)), ms: performance.now() - started }
    gl.deleteShader(fragment)
  } else {
    const program = gl.createProgram()

    gl.attachShader(program, engine.vertex)
    gl.attachShader(program, fragment)
    gl.bindAttribLocation(program, 0, 'vitrailCorner')
    gl.linkProgram(program)
    gl.deleteShader(fragment)

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      entry = { ok: false, log: cleanLog(gl.getProgramInfoLog(program)), ms: performance.now() - started }
      gl.deleteProgram(program)
    } else {
      const locations = new Map()

      entry = {
        ok: true,
        log: '',
        ms: performance.now() - started,
        program,
        at(name) {
          if (!locations.has(name)) {
            locations.set(name, gl.getUniformLocation(program, name))
          }

          return locations.get(name)
        }
      }
    }
  }

  engine.programs.set(source, entry)

  while (engine.programs.size > MAX_PROGRAMS) {
    const [oldest, old] = engine.programs.entries().next().value

    engine.programs.delete(oldest)

    if (old.program) {
      gl.deleteProgram(old.program)
    }
  }

  return entry
}

function cleanLog(log) {
  return String(log || 'Unknown compile error.')
    .replace(/\u0000/g, '')
    .trim()
    .slice(0, 4000)
}

function rgbOf(hex) {
  const value = HEX.test(hex) ? HEX.exec(hex)[1] : '000000'

  return [0, 2, 4].map(at => parseInt(value.slice(at, at + 2), 16) / 255)
}

/** Draw one frame of `entry` at w×h into the shared canvas (bottom-left corner). */
function drawFrame(entry, w, h, state, controls, values) {
  const gl = engine.gl
  const canvas = engine.canvas

  if (canvas.width < w || canvas.height < h) {
    canvas.width = Math.max(canvas.width, w)
    canvas.height = Math.max(canvas.height, h)
  }

  gl.viewport(0, 0, w, h)
  gl.useProgram(entry.program)
  gl.uniform3f(entry.at('iResolution'), w, h, 1)
  gl.uniform1f(entry.at('iTime'), state.time)
  gl.uniform1f(entry.at('iTimeDelta'), state.delta)
  gl.uniform1i(entry.at('iFrame'), state.frame)
  gl.uniform4f(entry.at('iMouse'), state.mouse[0], state.mouse[1], state.mouse[2], state.mouse[3])

  for (const control of controls) {
    const value = values[control.name]
    const at = entry.at(control.name)

    if (at === null) {
      continue
    }

    if (control.kind === 'slider') {
      gl.uniform1f(at, Number(value) || 0)
    } else if (control.kind === 'toggle') {
      gl.uniform1i(at, value ? 1 : 0)
    } else {
      const [r, g, b] = rgbOf(String(value))

      gl.uniform3f(at, r, g, b)
    }
  }

  gl.drawArrays(gl.TRIANGLES, 0, 3)
}

function blit(target, w, h) {
  const context = target.getContext('2d')

  if (target.width !== w || target.height !== h) {
    target.width = w
    target.height = h
  }

  context.drawImage(engine.canvas, 0, engine.canvas.height - h, w, h, 0, 0, w, h)
}

function scratchCanvas() {
  if (!engine.scratch) {
    engine.scratch = document.createElement('canvas')
  }

  return engine.scratch
}

/** A still as a data URL, or null. Used for the model's preview and for PNG export. */
function still(source, controls, values, w, h, time, type, quality) {
  const entry = programFor(source)

  if (!entry.ok) {
    return null
  }

  drawFrame(entry, w, h, { time, delta: 1 / 60, frame: 0, mouse: [0, 0, 0, 0] }, controls, values)
  const scratch = scratchCanvas()

  blit(scratch, w, h)

  return scratch.toDataURL(type, quality)
}

function tick(now) {
  engine.raf = 0
  const elapsed = engine.last ? Math.min((now - engine.last) / 1000, 0.1) : 1 / 60

  engine.last = now
  let animated = 0

  if (glContext() && engine.vertex && !document.hidden) {
    for (const player of engine.players) {
      if (!player.visible || !player.cssWidth || (!player.playing && !player.dirty)) {
        continue
      }

      const entry = programFor(player.source)

      if (!entry.ok) {
        player.dirty = false
        continue
      }

      if (player.playing) {
        player.state.time += elapsed
        player.state.delta = elapsed
        player.state.frame += 1
        animated += 1
      }

      const ratio = Math.min(window.devicePixelRatio || 1, 2) * (player.playing ? engine.quality : 1)
      const scale = Math.min(1, MAX_SIDE / (Math.max(player.cssWidth, player.cssHeight) * ratio))
      const w = Math.max(2, Math.round(player.cssWidth * ratio * scale))
      const h = Math.max(2, Math.round(player.cssHeight * ratio * scale))

      drawFrame(entry, w, h, player.state, player.controls, player.values)
      blit(player.canvas, w, h)
      player.dirty = false
      player.onFrame?.(elapsed, w, h)
    }

    // A shader too heavy for this GPU drops to a lower resolution instead of stalling the app.
    if (animated && elapsed > 0.034) {
      engine.slow += 1

      if (engine.slow > 20 && engine.quality > 0.35) {
        engine.quality *= 0.75
        engine.slow = 0
      }
    } else if (engine.slow > 0) {
      engine.slow -= 1
    }
  }

  if (animated || [...engine.players].some(player => player.dirty && player.visible)) {
    engine.raf = requestAnimationFrame(tick)
  } else {
    engine.last = 0
  }
}

function kick() {
  if (!engine.raf) {
    engine.raf = requestAnimationFrame(tick)
  }
}

function shutdown() {
  if (engine.raf) {
    cancelAnimationFrame(engine.raf)
  }

  engine.gl?.getExtension('WEBGL_lose_context')?.loseContext()
  Object.assign(engine, { canvas: null, gl: null, vertex: null, raf: 0, last: 0, quality: 1, slow: 0, scratch: null })
  engine.programs.clear()
  engine.players.clear()
}

// ── Plugin doors, captured at register ──────────────────────────────────────

let doors = null

function notify(kind, message) {
  try {
    host.notify({ kind, message })
  } catch {
    // an older host without toasts: nothing to say
  }
}

async function copyText(text) {
  const done = (await doors?.os?.writeClipboard?.(text)) === true

  notify(done ? 'success' : 'warning', done ? 'GLSL copied' : 'Could not reach the clipboard')
}

async function savePng(shaderId, source, controls, values, time) {
  const png = still(source, controls, values, 1920, 1080, time, 'image/png')

  if (!png || !doors) {
    notify('warning', 'This shader cannot be exported')

    return
  }

  try {
    const saved = await doors.rest('/snapshot', { method: 'POST', body: { id: shaderId, png } })

    notify('success', 'Saved a 1920 × 1080 PNG')

    if (saved?.path) {
      doors.os?.revealPath?.(saved.path)
    }
  } catch {
    notify('warning', 'Could not save the PNG. Is the Vitrail plugin enabled for this profile?')
  }
}

// ── UI ──────────────────────────────────────────────────────────────────────

const ICONS = {
  play: ['M7 4l13 8-13 8z'],
  pause: ['M7 4v16', 'M17 4v16'],
  restart: ['M3 12a9 9 0 1 0 3-6.7L3 8', 'M3 3v5h5'],
  camera: [
    'M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z',
    'M12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z'
  ],
  copy: ['M9 9h11v11H9z', 'M5 15H4V4h11v1'],
  expand: ['M8 3H3v5', 'M21 8V3h-5', 'M3 16v5h5', 'M16 21h5v-5'],
  trash: ['M3 6h18', 'M8 6V4h8v2', 'M19 6l-1 14H6L5 6'],
  fix: ['M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z'],
  reset: ['M4 4v6h6', 'M20 20v-6h-6', 'M20 9A8 8 0 0 0 5.6 6.6L4 10', 'M4 15a8 8 0 0 0 14.4 2.4L20 14']
}

function Icon({ name, size = 14 }) {
  return jsx('svg', {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': true,
    children: ICONS[name].map(d => jsx('path', { d }, d))
  })
}

function IconButton({ icon, label, onClick, danger }) {
  const [hover, setHover] = useState(false)

  return jsx('button', {
    type: 'button',
    title: label,
    'aria-label': label,
    onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      width: 26,
      height: 26,
      padding: 0,
      border: 'none',
      borderRadius: 6,
      cursor: 'pointer',
      color: danger ? 'var(--ui-red)' : 'var(--ui-text-primary)',
      background: hover ? 'var(--ui-control-hover-background)' : 'transparent'
    },
    children: jsx(Icon, { name: icon })
  })
}

function TextButton({ icon, children, onClick }) {
  return jsxs('button', {
    type: 'button',
    onClick,
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      padding: '5px 10px',
      borderRadius: 6,
      border: '1px solid var(--ui-stroke-secondary)',
      background: 'var(--ui-bg-elevated)',
      color: 'var(--ui-text-primary)',
      font: 'inherit',
      fontSize: 12,
      cursor: 'pointer'
    },
    children: [icon ? jsx(Icon, { name: icon }, 'icon') : null, children]
  })
}

function ControlRow({ control, value, onChange }) {
  const head = {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 8,
    fontSize: 11,
    color: 'var(--ui-text-secondary)'
  }

  if (control.kind === 'slider') {
    const digits = control.step >= 1 ? 0 : control.step >= 0.1 ? 1 : 2

    return jsxs('label', {
      style: { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 },
      children: [
        jsxs('span', {
          style: head,
          children: [
            jsx('span', { children: control.label }),
            jsx('span', {
              style: { fontVariantNumeric: 'tabular-nums', color: 'var(--ui-text-tertiary)' },
              children: Number(value).toFixed(digits)
            })
          ]
        }),
        jsx('input', {
          type: 'range',
          min: control.min,
          max: control.max,
          step: control.step,
          value,
          onChange: event => onChange(control.name, Number(event.target.value)),
          style: { width: '100%', margin: 0, accentColor: 'var(--ui-accent)' }
        })
      ]
    })
  }

  return jsxs('label', {
    style: { ...head, alignItems: 'center', minHeight: 26, cursor: 'pointer' },
    children: [
      jsx('span', { children: control.label }),
      control.kind === 'color'
        ? jsx('input', {
            type: 'color',
            value,
            onChange: event => onChange(control.name, event.target.value),
            style: { width: 34, height: 22, padding: 0, border: 'none', background: 'none', cursor: 'pointer' }
          })
        : jsx('input', {
            type: 'checkbox',
            checked: Boolean(value),
            onChange: event => onChange(control.name, event.target.checked),
            style: { margin: 0, accentColor: 'var(--ui-accent)' }
          })
    ]
  })
}

/**
 * One live shader. `mode`: 'card' (in the transcript), 'hero' (gallery detail) or 'tile'
 * (gallery grid: a still that plays under the pointer).
 */
function ShaderPlayer({ source, title, shaderId, sessionId, mode = 'card', onDeleted }) {
  const controls = useMemo(() => parseControls(source), [source])
  const [values, setValues] = useState(() => defaultsOf(controls))
  const [status, setStatus] = useState(null)
  const [playing, setPlaying] = useState(mode !== 'tile')
  const [hover, setHover] = useState(false)
  const [readout, setReadout] = useState('')
  const [sent, setSent] = useState(false)
  const canvasRef = useRef(null)
  const stageRef = useRef(null)
  const playerRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const stage = stageRef.current

    if (!canvas || !stage) {
      return undefined
    }

    const entry = programFor(source)

    setStatus({ ok: entry.ok, log: entry.log })
    let frames = 0
    let span = 0
    const player = {
      canvas,
      source,
      controls,
      values: defaultsOf(controls),
      state: { time: mode === 'tile' ? 2 : 0, delta: 1 / 60, frame: 0, mouse: [0, 0, 0, 0] },
      playing: mode !== 'tile',
      dirty: true,
      visible: false,
      cssWidth: 0,
      cssHeight: 0,
      onFrame(elapsed, w, h) {
        frames += 1
        span += elapsed

        if (span >= 0.5) {
          setReadout(`${Math.round(frames / span)} fps · ${w} × ${h}`)
          frames = 0
          span = 0
        }
      }
    }

    playerRef.current = player
    engine.players.add(player)
    engine.quality = 1

    const resize = new ResizeObserver(() => {
      const box = stage.getBoundingClientRect()

      player.cssWidth = box.width
      player.cssHeight = box.height
      player.dirty = true
      kick()
    })
    const watch = new IntersectionObserver(entries => {
      player.visible = entries.some(item => item.isIntersecting)

      if (player.visible) {
        player.dirty = true
        kick()
      }
    })

    resize.observe(stage)
    watch.observe(stage)

    return () => {
      resize.disconnect()
      watch.disconnect()
      engine.players.delete(player)
      playerRef.current = null
    }
  }, [source, controls, mode])

  useEffect(() => {
    const player = playerRef.current

    if (player) {
      player.values = values
      player.playing = playing
      player.dirty = true
      kick()
    }
  }, [values, playing, status])

  const pointer = event => {
    const player = playerRef.current
    const canvas = canvasRef.current

    if (!player || !canvas) {
      return null
    }

    const box = canvas.getBoundingClientRect()

    return [
      ((event.clientX - box.left) * canvas.width) / box.width,
      ((box.bottom - event.clientY) * canvas.height) / box.height
    ]
  }

  const onPointerDown = event => {
    const at = pointer(event)

    if (at && mode !== 'tile') {
      const mouse = playerRef.current.state.mouse

      mouse[0] = mouse[2] = at[0]
      mouse[1] = mouse[3] = at[1]
      event.currentTarget.setPointerCapture?.(event.pointerId)
      playerRef.current.dirty = true
      kick()
    }
  }

  const onPointerMove = event => {
    if (event.buttons && mode !== 'tile') {
      const at = pointer(event)

      if (at) {
        playerRef.current.state.mouse[0] = at[0]
        playerRef.current.state.mouse[1] = at[1]
        playerRef.current.dirty = true
        kick()
      }
    }
  }

  const onPointerUp = () => {
    const mouse = playerRef.current?.state.mouse

    if (mouse) {
      mouse[2] = -Math.abs(mouse[2])
      mouse[3] = -Math.abs(mouse[3])
    }
  }

  const restart = () => {
    const player = playerRef.current

    if (player) {
      player.state.time = 0
      player.state.frame = 0
      player.dirty = true
      kick()
    }
  }

  const fullscreen = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen?.()
    } else {
      stageRef.current?.requestFullscreen?.().catch(() => notify('warning', 'Full screen is not available here'))
    }
  }

  const askFix = () => {
    const text =
      `The Vitrail shader "${title}"${shaderId ? ` (${shaderId})` : ''} did not compile on my GPU:\n\n` +
      `${status?.log || ''}\n\nFix the source and render it again.`
    const delivered = host.composer?.submit?.(sessionId ?? null, text) === true

    setSent(delivered)

    if (!delivered) {
      notify('warning', 'Open that chat to send the error to Hermes')
    }
  }

  const failed = status && !status.ok
  const tile = mode === 'tile'
  const stage = jsxs('div', {
    ref: stageRef,
    onMouseEnter: () => {
      setHover(true)

      if (tile) {
        setPlaying(true)
      }
    },
    onMouseLeave: () => {
      setHover(false)

      if (tile) {
        setPlaying(false)
      }
    },
    style: {
      position: 'relative',
      width: '100%',
      aspectRatio: '16 / 9',
      overflow: 'hidden',
      background: 'var(--ui-bg-secondary)'
    },
    children: [
      jsx(
        'canvas',
        {
          ref: canvasRef,
          onPointerDown,
          onPointerMove,
          onPointerUp,
          style: { display: failed ? 'none' : 'block', width: '100%', height: '100%', touchAction: 'none' }
        },
        'canvas'
      ),
      failed
        ? jsxs(
            'div',
            {
              style: {
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
                padding: 14,
                overflow: 'auto'
              },
              children: [
                jsx('div', {
                  style: { fontSize: 12, fontWeight: 600, color: 'var(--ui-red)' },
                  children: 'This shader did not compile on this GPU'
                }),
                jsx('pre', {
                  style: {
                    margin: 0,
                    flex: 1,
                    whiteSpace: 'pre-wrap',
                    fontSize: 11,
                    lineHeight: 1.5,
                    color: 'var(--ui-text-secondary)'
                  },
                  children: status.log
                }),
                tile
                  ? null
                  : jsx('div', {
                      children: jsx(TextButton, {
                        icon: 'fix',
                        onClick: askFix,
                        children: sent ? 'Sent to Hermes' : 'Ask Hermes to fix it'
                      })
                    })
              ]
            },
            'error'
          )
        : null,
      !failed && !tile
        ? jsxs(
            'div',
            {
              style: {
                position: 'absolute',
                left: 8,
                right: 8,
                bottom: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 2,
                padding: 3,
                borderRadius: 9,
                border: '1px solid var(--ui-stroke-tertiary)',
                background: 'color-mix(in srgb, var(--ui-bg-elevated) 80%, transparent)',
                backdropFilter: 'blur(10px)',
                opacity: hover || !playing ? 1 : 0,
                transition: 'opacity 160ms ease',
                pointerEvents: hover || !playing ? 'auto' : 'none'
              },
              children: [
                jsx(IconButton, {
                  icon: playing ? 'pause' : 'play',
                  label: playing ? 'Pause' : 'Play',
                  onClick: () => setPlaying(!playing)
                }),
                jsx(IconButton, { icon: 'restart', label: 'Restart from 0 s', onClick: restart }),
                jsx('span', {
                  style: {
                    flex: 1,
                    padding: '0 6px',
                    fontSize: 11,
                    fontVariantNumeric: 'tabular-nums',
                    color: 'var(--ui-text-tertiary)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  },
                  children: readout
                }),
                jsx(IconButton, { icon: 'copy', label: 'Copy the GLSL source', onClick: () => copyText(source) }),
                shaderId
                  ? jsx(IconButton, {
                      icon: 'camera',
                      label: 'Save a 1920 × 1080 PNG of this frame',
                      onClick: () =>
                        savePng(shaderId, source, controls, values, playerRef.current?.state.time ?? 0)
                    })
                  : null,
                jsx(IconButton, { icon: 'expand', label: 'Full screen', onClick: fullscreen }),
                onDeleted
                  ? jsx(IconButton, { icon: 'trash', label: 'Delete from the library', danger: true, onClick: onDeleted })
                  : null
              ]
            },
            'bar'
          )
        : null
    ]
  })

  if (tile) {
    return stage
  }

  const changed = controls.some(control => values[control.name] !== control.value)

  return jsxs('figure', {
    style: {
      margin: mode === 'card' ? '8px 0' : 0,
      width: '100%',
      maxWidth: mode === 'card' ? 760 : 'none',
      borderRadius: 12,
      overflow: 'hidden',
      border: '1px solid var(--ui-stroke-secondary)',
      background: 'var(--ui-bg-card)'
    },
    children: [
      stage,
      controls.length && !failed
        ? jsx(
            'div',
            {
              style: {
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
                gap: '8px 18px',
                padding: '10px 12px 4px'
              },
              children: controls.map(control =>
                jsx(
                  ControlRow,
                  {
                    control,
                    value: values[control.name],
                    onChange: (name, value) => setValues(current => ({ ...current, [name]: value }))
                  },
                  control.name
                )
              )
            },
            'controls'
          )
        : null,
      jsxs(
        'figcaption',
        {
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 12px',
            fontSize: 12,
            color: 'var(--ui-text-secondary)'
          },
          children: [
            jsx('span', {
              style: {
                flex: 1,
                fontWeight: 600,
                color: 'var(--ui-text-primary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap'
              },
              children: title
            }),
            changed
              ? jsx(IconButton, {
                  icon: 'reset',
                  label: 'Reset the controls',
                  onClick: () => setValues(defaultsOf(controls))
                })
              : null,
            jsx('span', { style: { color: 'var(--ui-text-tertiary)' }, children: 'Vitrail' })
          ]
        },
        'caption'
      )
    ]
  })
}

// ── Transcript card ─────────────────────────────────────────────────────────

function ShaderCard({ file, args, sessionId, fallback }) {
  const code = typeof args?.code === 'string' ? args.code : ''
  const source = useMemo(() => (code.trim() ? normalize(code) : ''), [code])

  if (!source || !/\.html$/i.test(file?.name || '')) {
    return fallback
  }

  const id = String(file.name).replace(/\.html$/i, '')
  const title = typeof args.title === 'string' && args.title.trim() ? args.title.trim().slice(0, 80) : 'Untitled shader'

  return jsx(ShaderPlayer, { source, title, shaderId: ID_RE.test(id) ? id : null, sessionId, mode: 'card' })
}

// A Desktop without tool media renderers draws the card from `::vitrail{id="…"}`. A directive
// carries strings only, so the source comes from the plugin's own backend.
function DirectiveCard({ id, sessionId }) {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let live = true

    if (!doors || !ID_RE.test(id)) {
      setState({ status: 'missing' })

      return undefined
    }

    setState({ status: 'loading' })
    doors.rest(`/shaders/${id}`).then(
      data =>
        live &&
        setState(
          typeof data?.code === 'string' && data.code.trim()
            ? { status: 'ready', source: normalize(data.code), title: String(data.title || id).slice(0, 80) }
            : { status: 'missing' }
        ),
      () => live && setState({ status: 'missing' })
    )

    return () => {
      live = false
    }
  }, [id])

  if (state.status === 'ready') {
    return jsx(ShaderPlayer, { source: state.source, title: state.title, shaderId: id, sessionId, mode: 'card' })
  }

  return jsx('div', {
    style: {
      margin: '8px 0',
      padding: '10px 12px',
      maxWidth: 760,
      borderRadius: 12,
      border: '1px solid var(--ui-stroke-secondary)',
      fontSize: 12,
      color: 'var(--ui-text-tertiary)'
    },
    children:
      state.status === 'loading'
        ? 'Loading the shader…'
        : 'This Vitrail shader is not in the library of this profile any more.'
  })
}

// ── Gallery page ────────────────────────────────────────────────────────────

function Gallery() {
  const [state, setState] = useState({ status: 'loading', shaders: [] })
  const [open, setOpen] = useState(null)
  const [stamp, setStamp] = useState(0)

  useEffect(() => {
    let live = true

    if (!doors) {
      return undefined
    }

    doors.rest('/shaders').then(
      data => live && setState({ status: 'ready', shaders: Array.isArray(data?.shaders) ? data.shaders : [] }),
      () => live && setState({ status: 'offline', shaders: [] })
    )
    const stop = doors.onEvent?.('plugin.vitrail.compile', () => doors?.setTimeout(() => live && setStamp(Date.now()), 1500))

    return () => {
      live = false
      stop?.()
    }
  }, [stamp])

  const shaders = state.shaders.filter(item => item && typeof item.code === 'string' && ID_RE.test(String(item.id)))
  const current = shaders.find(item => item.id === open) || null

  const remove = async id => {
    try {
      await doors.rest(`/shaders/${id}`, { method: 'DELETE' })
      setOpen(null)
      setStamp(Date.now())
    } catch {
      notify('warning', 'Could not delete this shader')
    }
  }

  const message = text =>
    jsx('div', {
      style: { padding: '48px 12px', textAlign: 'center', fontSize: 13, color: 'var(--ui-text-tertiary)' },
      children: text
    })

  return jsx('div', {
    style: { height: '100%', overflow: 'auto' },
    children: jsxs('div', {
      style: { maxWidth: 1100, margin: '0 auto', padding: '20px 20px 40px', display: 'flex', flexDirection: 'column', gap: 16 },
      children: [
        jsxs(
          'div',
          {
            children: [
              jsx('div', {
                style: { fontSize: 18, fontWeight: 600, color: 'var(--ui-text-primary)' },
                children: 'Vitrail'
              }),
              jsx('div', {
                style: { fontSize: 12, color: 'var(--ui-text-tertiary)' },
                children: 'Every shader Hermes has rendered for you. Hover to play, click to open.'
              })
            ]
          },
          'head'
        ),
        current
          ? jsx(
              ShaderPlayer,
              {
                source: normalize(current.code),
                title: current.title || current.id,
                shaderId: current.id,
                sessionId: null,
                mode: 'hero',
                onDeleted: () => remove(current.id)
              },
              current.id
            )
          : null,
        state.status === 'loading' ? message('Loading the library…') : null,
        state.status === 'offline'
          ? message('The library is not reachable. Enable the vitrail plugin for this profile: hermes plugins enable vitrail')
          : null,
        state.status === 'ready' && !shaders.length
          ? message('No shaders yet. Ask Hermes for one: "a shader of a lagoon at dusk, with sliders".')
          : null,
        shaders.length
          ? jsx(
              'div',
              {
                style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 },
                children: shaders.map(item =>
                  jsxs(
                    'button',
                    {
                      type: 'button',
                      onClick: () => setOpen(item.id === open ? null : item.id),
                      style: {
                        display: 'block',
                        padding: 0,
                        textAlign: 'left',
                        font: 'inherit',
                        cursor: 'pointer',
                        borderRadius: 10,
                        overflow: 'hidden',
                        background: 'var(--ui-bg-card)',
                        border:
                          item.id === open ? '1px solid var(--ui-accent)' : '1px solid var(--ui-stroke-secondary)'
                      },
                      children: [
                        jsx(ShaderPlayer, { source: normalize(item.code), title: item.title, mode: 'tile' }, 'player'),
                        jsx(
                          'div',
                          {
                            style: {
                              padding: '7px 10px',
                              fontSize: 12,
                              color: 'var(--ui-text-primary)',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap'
                            },
                            children: item.title || item.id
                          },
                          'title'
                        )
                      ]
                    },
                    item.id
                  )
                )
              },
              'grid'
            )
          : null
      ]
    })
  })
}

// ── GPU check for vitrail_render ────────────────────────────────────────────

function verify(payload) {
  const id = String(payload?.id || '')

  if (!ID_RE.test(id) || typeof payload?.code !== 'string' || !doors) {
    return
  }

  const source = normalize(payload.code)
  const entry = programFor(source)

  if (entry.transient) {
    return // no WebGL here: stay silent so the tool reports "not verified"
  }

  const report = { id, ok: entry.ok, log: entry.log, compile_ms: Math.round(entry.ms) }

  if (entry.ok) {
    try {
      const controls = parseControls(source)
      const started = performance.now()

      report.poster = still(source, controls, defaultsOf(controls), 640, 360, 2, 'image/jpeg', 0.8)
      engine.gl.finish()
      report.frame_ms = Math.round((performance.now() - started) * 10) / 10
    } catch {
      // the compile verdict is what matters
    }
  }

  doors.rest('/report', { method: 'POST', body: report }).catch(() => {})
}

// ── Plugin ──────────────────────────────────────────────────────────────────

export default {
  id: 'vitrail',
  name: 'Vitrail',
  register(ctx) {
    doors = ctx
    ctx.onDispose(() => {
      shutdown()
      doors = null
    })

    if (typeof sdk.TOOL_MEDIA_RENDERER_AREA === 'string') {
      ctx.register({
        id: 'shader-card',
        area: sdk.TOOL_MEDIA_RENDERER_AREA,
        data: { tools: [TOOL], render: props => jsx(ShaderCard, props) }
      })
    }

    if (typeof sdk.TRANSCRIPT_DIRECTIVE_AREA === 'string') {
      ctx.register({
        id: 'shader-directive',
        area: sdk.TRANSCRIPT_DIRECTIVE_AREA,
        data: {
          name: 'vitrail',
          render: ({ attrs, sessionId }) => {
            const id = typeof attrs?.id === 'string' ? attrs.id : ''

            return ID_RE.test(id) ? jsx(DirectiveCard, { id, sessionId: sessionId ?? null }, id) : null
          }
        }
      })
    }

    ctx.registerMany([
      { id: 'page', area: sdk.ROUTES_AREA, data: { path: '/vitrail' }, render: () => jsx(Gallery, {}) },
      { id: 'nav', area: sdk.SIDEBAR_NAV_AREA, data: { path: '/vitrail', label: 'Vitrail', codicon: 'symbol-color' } },
      {
        id: 'open',
        area: sdk.PALETTE_AREA,
        data: {
          id: 'vitrail.open',
          label: 'Open the Vitrail shader gallery',
          keywords: ['vitrail', 'shader', 'glsl', 'gallery'],
          run: () => host.navigate('/vitrail')
        }
      }
    ])

    // The agent half waits for a GPU verdict only while a Desktop window says it is listening.
    // It also says which transcript surface this Desktop has, so the tool picks the right one.
    const surfaces = {
      toolMedia: typeof sdk.TOOL_MEDIA_RENDERER_AREA === 'string',
      directive: typeof sdk.TRANSCRIPT_DIRECTIVE_AREA === 'string'
    }
    const hello = () => ctx.rest('/hello', { method: 'POST', body: surfaces }).catch(() => {})

    hello()
    ctx.setInterval(hello, 60_000)
    ctx.onEvent('plugin.vitrail.compile', event => verify(event?.payload))
  }
}
