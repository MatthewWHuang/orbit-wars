"""Custom Orbit Wars visualizer.

Generates a self-contained HTML file with playback controls, planet trails,
per-player resource graphs, and per-agent debug info parsed from stderr.
"""

import html as _html
import json
import os


PLAYER_COLORS = ["#3aa6ff", "#ff5a5a", "#3ad17a", "#ffd23a"]
NEUTRAL_COLOR = "#888888"


def _extract_frames(env_json):
    """Reduce env.toJSON() to the minimal per-step data the viewer needs."""
    frames = []
    steps = env_json.get("steps", [])
    for step_idx, step in enumerate(steps):
        # Player 0's observation has the full state. (All players see the same
        # board; "player" field just identifies who is reading.)
        obs = step[0].get("observation", {}) if step else {}
        frames.append({
            "step": step_idx,
            "planets": obs.get("planets", []),
            "fleets": obs.get("fleets", []),
            "comets": obs.get("comets", []),
            "comet_planet_ids": obs.get("comet_planet_ids", []),
            "angular_velocity": obs.get("angular_velocity", 0),
            "rewards": [s.get("reward") for s in step],
            "statuses": [s.get("status") for s in step],
            "actions": [s.get("action") for s in step],
        })
    return frames


def _extract_debug(env_json):
    """Pull per-step JSON debug lines from agent stderr (env.info['logs'])."""
    info = env_json.get("info") or {}
    logs = info.get("logs") or []
    n_agents = max((len(s) for s in env_json.get("steps", [])), default=0)
    # logs[step][agent] = {"stdout": str, "stderr": str, "duration": float}
    debug = []
    for step_logs in logs:
        per_agent = []
        for a in range(n_agents):
            entry = step_logs[a] if a < len(step_logs) else None
            d = None
            if entry and isinstance(entry, dict):
                err = entry.get("stderr") or ""
                # Parse the last JSON line, if any
                for line in reversed(err.splitlines()):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            d = json.loads(line)
                            break
                        except Exception:
                            pass
            per_agent.append(d)
        debug.append(per_agent)
    return debug


def _pretty_bot_name(b):
    if not b:
        return ""
    if b == "random":
        return "random"
    # Strip trailing "/main.py" or "\main.py"
    base = b.replace("\\", "/")
    if base.endswith("/main.py"):
        base = base[: -len("/main.py")]
    elif base.endswith(".py"):
        base = base[:-3]
    return os.path.basename(base) or b


def _build_match(env, bot_names=None, label=None, seed=None):
    """Build a single match payload from an env (or env.toJSON() dict)."""
    env_json = env.toJSON() if hasattr(env, "toJSON") else env
    frames = _extract_frames(env_json)
    debug = _extract_debug(env_json)
    n_agents = len(frames[-1]["rewards"]) if frames else 0
    names = [_pretty_bot_name(b) for b in (bot_names or [])]
    while len(names) < n_agents:
        names.append(f"Player {len(names)}")
    rewards = frames[-1]["rewards"] if frames else []
    if label is None:
        bot_label = " vs ".join(names[:n_agents])
        label = f"seed {seed}: {bot_label}" if seed is not None else bot_label
    return {
        "frames": frames,
        "debug": debug,
        "n_agents": n_agents,
        "colors": PLAYER_COLORS[:max(n_agents, 1)],
        "names": names[:n_agents],
        "rewards": rewards,
        "label": label,
        "seed": seed,
    }


def write_html(env, out_path, bot_names=None, auto_cinema=False):
    match = _build_match(env, bot_names=bot_names)
    return _write_multi(out_path, [match], auto_cinema=auto_cinema)


def write_tournament_html(matches, out_path, auto_cinema=False):
    """matches: list of dicts {env_json|env, bot_names, seed} OR pre-built match payloads."""
    built = []
    for m in matches:
        if "frames" in m:
            built.append(m)
        else:
            built.append(_build_match(m["env"], bot_names=m.get("bot_names"), seed=m.get("seed")))
    return _write_multi(out_path, built, auto_cinema=auto_cinema)


def _write_multi(out_path, matches, auto_cinema=False):
    payload = {"matches": matches, "autoCinema": bool(auto_cinema)}
    html = _TEMPLATE.replace(
        "__PAYLOAD__", _html.escape(json.dumps(payload), quote=False)
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.abspath(out_path)


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Orbit Wars Viewer</title>
<style>
  :root {
    --bg: #0b1020; --panel: #131a30; --ink: #e8edf7; --muted: #8a96b8;
    --grid: #1f2a4a;
  }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font-family: ui-sans-serif, system-ui, sans-serif; height: 100%; }
  .wrap { display: grid; grid-template-columns: 320px 1fr; grid-template-rows:
    1fr 180px auto; gap: 8px; padding: 8px; height: 100vh; box-sizing: border-box; }
  .side { grid-row: 1 / span 2; background: var(--panel); border-radius: 8px;
    padding: 12px; overflow-y: auto; font-size: 13px; display: flex;
    flex-direction: column; gap: 10px; }
  .tabs { display: flex; flex-direction: column; gap: 4px; max-height: 35%;
    overflow-y: auto; border-bottom: 1px solid #2a3560; padding-bottom: 8px; }
  .tabs .tab { padding: 6px 8px; border-radius: 4px; cursor: pointer;
    background: #1a223e; font-size: 12px; line-height: 1.3; }
  .tabs .tab:hover { background: #2a3560; }
  .tabs .tab.active { background: #2a3560; outline: 1px solid #ffd966; }
  .tabs .tab .label { display: block; }
  .tabs .tab .res { display: block; color: #8a96b8; font-size: 10px;
    margin-top: 2px; }
  .tabs.hidden { display: none; }
  .board { background: var(--panel); border-radius: 8px; position: relative;
    display: flex; align-items: center; justify-content: center; }
  .graphs { grid-column: 2; display: grid; grid-template-columns: 1fr 1fr 1fr 1fr;
    gap: 8px; }
  .cinema-hype { grid-column: 2; background: #131a30; border-radius: 8px;
    padding: 8px; display: none; min-width: 0; min-height: 0; }
  .cinema-hype canvas { width: 100%; height: 100%; display: block; }
  .cinema-active .graphs { display: none; }
  .cinema-active .side { display: none; }
  .cinema-active .controls { display: none; }
  .cinema-active .wrap { grid-template-columns: 1fr; grid-template-rows: 1fr;
    padding: 0; gap: 0; height: 100vh; }
  .cinema-active .board { border-radius: 0; }
  .cinema-active .cinema-hype { display: block; position: fixed; left: 0;
    right: 0; bottom: 0; height: 22vh; background: linear-gradient(to top,
    rgba(8,12,28,0.85) 0%, rgba(8,12,28,0.65) 60%, rgba(8,12,28,0) 100%);
    border-radius: 0; padding: 24px 32px; box-sizing: border-box;
    pointer-events: none; z-index: 10; }
  #cinemaExit { display: none; position: fixed; top: 18px; right: 18px;
    background: rgba(20,28,56,0.85); color: #e8edf7; border: 0;
    padding: 10px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
    backdrop-filter: blur(4px); z-index: 20; }
  #cinemaExit:hover { background: rgba(40,52,96,0.9); }
  .cinema-active #cinemaExit { display: block; }
  .graph-panel { background: var(--panel); border-radius: 8px; padding: 8px;
    display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .graph-panel .title { color: var(--muted); font-size: 11px;
    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
  .graph-panel canvas { flex: 1; width: 100%; height: 100%; }
  .controls { grid-column: 1 / span 2; background: var(--panel); border-radius: 8px;
    padding: 8px 12px; display: flex; gap: 12px; align-items: center; }
  canvas { display: block; }
  button { background: #2a3560; color: var(--ink); border: 0; padding: 6px 12px;
    border-radius: 4px; cursor: pointer; font-size: 13px; }
  button:hover { background: #3a4680; }
  input[type=range] { flex: 1; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600; margin-right: 4px; }
  .stat-row { display: flex; justify-content: space-between; padding: 2px 0; }
  .stat-row .lbl { color: var(--muted); }
  h3 { margin: 0 0 6px 0; font-size: 13px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 1px; }
  pre { font-size: 11px; background: #0a0f22; padding: 6px; border-radius: 4px;
    margin: 4px 0 12px 0; white-space: pre-wrap; word-break: break-word;
    max-height: 120px; overflow-y: auto; }
  label { font-size: 12px; color: var(--muted); }
</style>
</head><body>
<div class="wrap">
  <div class="side">
    <div class="tabs" id="tabList"></div>
    <div id="sideContent" style="flex:1;overflow-y:auto;"></div>
  </div>
  <div class="board"><canvas id="board"></canvas></div>
  <div class="graphs">
    <div class="graph-panel"><div class="title" id="g0title">Ships</div><canvas id="g0"></canvas></div>
    <div class="graph-panel"><div class="title" id="g1title">Production</div><canvas id="g1"></canvas></div>
    <div class="graph-panel"><div class="title" id="g2title">Planets</div><canvas id="g2"></canvas></div>
    <div class="graph-panel" id="g3panel"><div class="title" id="g3title">Ship gain delta</div><canvas id="g3"></canvas></div>
  </div>
  <div class="cinema-hype"><canvas id="hypeCanvas"></canvas></div>
  <button id="cinemaExit">✕ Exit Cinema</button>
  <div class="controls">
    <button id="playBtn">Play</button>
    <button id="prevBtn">&lt;</button>
    <button id="nextBtn">&gt;</button>
    <input type="range" id="scrub" min="0" value="0">
    <span id="stepLbl" style="min-width:90px;text-align:right;font-variant-numeric:tabular-nums;"></span>
    <label>Speed <select id="speedSel">
      <option value="1">1x</option><option value="2">2x</option>
      <option value="4" selected>4x</option><option value="8">8x</option>
      <option value="16">16x</option></select></label>
    <label><input type="checkbox" id="trailsChk" checked> Trails</label>
    <label><input type="checkbox" id="prodChk"> Production</label>
    <button id="cinemaBtn" style="background:#d8377c">Cinema</button>
  </div>
</div>
<script>
const DATA = JSON.parse(document.getElementById === undefined ? "{}" : `__PAYLOAD__`);
const MATCHES = DATA.matches;
let currentMatchIdx = 0;
let frames, debug, N_AGENTS, COLORS, NAMES, series;
const NEUTRAL = "#888";
const TRAIL_LEN = 30;

const board = document.getElementById('board');
const bctx = board.getContext('2d');
const graphCanvases = [
  document.getElementById('g0'),
  document.getElementById('g1'),
  document.getElementById('g2'),
  document.getElementById('g3'),
];
const graphCtxs = graphCanvases.map(c => c.getContext('2d'));
const graphTitles = ['Ships', 'Production', 'Planets', 'Ship delta'];
const deltaPanel = document.getElementById('g3panel');
const hypeCanvas = document.getElementById('hypeCanvas');
const hctx = hypeCanvas.getContext('2d');
const sideContent = document.getElementById('sideContent');
const tabList = document.getElementById('tabList');
const scrub = document.getElementById('scrub');
const stepLbl = document.getElementById('stepLbl');
const playBtn = document.getElementById('playBtn');
const trailsChk = document.getElementById('trailsChk');
const prodChk = document.getElementById('prodChk');

let hoveredFleet = null;       // fleet array from current frame
let hoverPrediction = null;    // { traj: [[x,y],...], hitPlanet, hitPos, hitStep, fate }
let selectedFleetId = null;    // pinned fleet id (survives across frames)
let selectedPrediction = null; // prediction for the selected fleet in current frame
let hoveredPlanetId = null;    // planet id under the cursor
let selectedPlanetId = null;   // pinned planet id
const SUN_C = 50, SUN_R = 10, BOARD_SIZE = 100, ROT_LIMIT = 50, MAX_SPEED = 6;

function fleetSpeed(ships) {
  if (ships <= 1) return 1;
  const s = 1 + (MAX_SPEED - 1) * Math.pow(Math.log(ships) / Math.log(1000), 1.5);
  return Math.min(s, MAX_SPEED);
}

function isOrbiting(p) {
  const dx = p[2] - SUN_C, dy = p[3] - SUN_C;
  return Math.sqrt(dx*dx + dy*dy) + p[4] < ROT_LIMIT;
}

// Continuous swept-pair collision: does fleet A->B come within r of planet P0->P1?
function sweptHit(A, B, P0, P1, r) {
  const d0x = A[0]-P0[0], d0y = A[1]-P0[1];
  const dvx = (B[0]-A[0])-(P1[0]-P0[0]);
  const dvy = (B[1]-A[1])-(P1[1]-P0[1]);
  const a = dvx*dvx + dvy*dvy;
  const b = 2*(d0x*dvx + d0y*dvy);
  const c = d0x*d0x + d0y*d0y - r*r;
  if (a < 1e-12) return { hit: c <= 0, t: 0 };
  const disc = b*b - 4*a*c;
  if (disc < 0) return { hit: false };
  const sq = Math.sqrt(disc);
  const t1 = (-b - sq) / (2*a);
  const t2 = (-b + sq) / (2*a);
  if (t2 < 0 || t1 > 1) return { hit: false };
  return { hit: true, t: Math.max(0, t1) };
}

function pointSegDist(p, v, w) {
  const l2 = (v[0]-w[0])**2 + (v[1]-w[1])**2;
  if (l2 === 0) return Math.hypot(p[0]-v[0], p[1]-v[1]);
  const t = Math.max(0, Math.min(1, ((p[0]-v[0])*(w[0]-v[0]) + (p[1]-v[1])*(w[1]-v[1])) / l2));
  return Math.hypot(p[0] - (v[0]+t*(w[0]-v[0])), p[1] - (v[1]+t*(w[1]-v[1])));
}

function predictFleet(fleet, frame, maxSteps = 200) {
  const [, owner, fx, fy, angle, , ships] = fleet;
  const speed = fleetSpeed(ships);
  const av = frame.angular_velocity || 0;
  // Build comet lookup: pid -> { path: [[x,y],...], baseIdx }
  // The engine increments path_index by 1 each tick before reading the new
  // position, so at prediction-step t the new pos is path[baseIdx + t].
  const cometInfo = {};
  for (const g of (frame.comets || [])) {
    const idx = g.path_index;
    const ids = g.planet_ids || [];
    const paths = g.paths || [];
    for (let i = 0; i < ids.length; i++) {
      cometInfo[ids[i]] = { path: paths[i] || [], baseIdx: idx };
    }
  }
  // Snapshot of planets we will advance per tick.
  const planets = frame.planets.map(p => ({
    id: p[0], owner: p[1], x: p[2], y: p[3], r: p[4], ships: p[5], prod: p[6],
    orbiting: isOrbiting(p),
    comet: cometInfo[p[0]] || null,
    expired: false,
  }));
  let pos = [fx, fy];
  const traj = [pos.slice()];

  for (let step = 1; step <= maxSteps; step++) {
    const next = [pos[0] + Math.cos(angle) * speed, pos[1] + Math.sin(angle) * speed];
    // Advance planet positions
    const planetsNext = planets.map(p => {
      if (p.expired) return { ...p };
      if (p.comet) {
        const ni = p.comet.baseIdx + step;
        if (ni < p.comet.path.length) {
          return { ...p, x: p.comet.path[ni][0], y: p.comet.path[ni][1] };
        }
        // Past end of path -- comet expired; engine removes it after this tick.
        return { ...p, expired: true };
      }
      if (!p.orbiting) return { ...p };
      const dx = p.x - SUN_C, dy = p.y - SUN_C;
      const cosA = Math.cos(av), sinA = Math.sin(av);
      return { ...p, x: SUN_C + dx*cosA - dy*sinA, y: SUN_C + dx*sinA + dy*cosA };
    });
    // Check planet collisions (closest along the swept segment wins).
    // Skip planets that have already expired in our simulation.
    let best = null;
    for (let i = 0; i < planets.length; i++) {
      if (planets[i].expired) continue;
      const h = sweptHit(pos, next, [planets[i].x, planets[i].y], [planetsNext[i].x, planetsNext[i].y], planets[i].r);
      if (h.hit && (best === null || h.t < best.t)) {
        best = { t: h.t, idx: i };
      }
    }
    if (best) {
      const i = best.idx;
      const hx = pos[0] + (next[0]-pos[0]) * best.t;
      const hy = pos[1] + (next[1]-pos[1]) * best.t;
      const px = planets[i].x + (planetsNext[i].x - planets[i].x) * best.t;
      const py = planets[i].y + (planetsNext[i].y - planets[i].y) * best.t;
      traj.push([hx, hy]);
      return { traj, hitPlanet: { ...planets[i], x: px, y: py }, hitPos: [hx, hy], hitStep: step, fate: 'planet' };
    }
    // Out of bounds?
    if (next[0] < 0 || next[0] > BOARD_SIZE || next[1] < 0 || next[1] > BOARD_SIZE) {
      traj.push(next);
      return { traj, hitPlanet: null, hitPos: next, hitStep: step, fate: 'out_of_bounds' };
    }
    // Sun?
    if (pointSegDist([SUN_C, SUN_C], pos, next) < SUN_R) {
      traj.push(next);
      return { traj, hitPlanet: null, hitPos: next, hitStep: step, fate: 'sun' };
    }
    pos = next;
    traj.push(pos.slice());
    for (let i = 0; i < planets.length; i++) {
      planets[i].x = planetsNext[i].x; planets[i].y = planetsNext[i].y;
    }
  }
  return { traj, hitPlanet: null, hitPos: pos, hitStep: maxSteps, fate: 'timeout' };
}
const speedSel = document.getElementById('speedSel');

let cur = 0;
let maxViewed = 0;   // furthest step the user has scrubbed/played to (anti-spoiler)
let playing = false;
let lastT = 0;
let regularShake = 0;     // screen-shake magnitude for the non-cinema board view
let regularShakeRAF = null;
let lastShakeStep = -1;   // last step we processed for shake-worthy events

function resizeCanvases() {
  const b = board.parentElement.getBoundingClientRect();
  if (cinemaMode) {
    // Fill the viewport in cinema mode -- bg/starfield extend to all edges,
    // game elements get drawn in a centered square sub-region.
    board.width = Math.max(1, Math.floor(b.width));
    board.height = Math.max(1, Math.floor(b.height));
  } else {
    const s = Math.min(b.width, b.height) - 8;
    board.width = s; board.height = s;
  }
  for (const c of graphCanvases) {
    const r = c.getBoundingClientRect();
    c.width = Math.max(1, Math.floor(r.width));
    c.height = Math.max(1, Math.floor(r.height));
  }
  if (hypeCanvas.offsetParent !== null) {
    const r = hypeCanvas.getBoundingClientRect();
    hypeCanvas.width = Math.max(1, Math.floor(r.width));
    hypeCanvas.height = Math.max(1, Math.floor(r.height));
  }
  draw();
}
window.addEventListener('resize', resizeCanvases);

function colorFor(owner) {
  if (owner === null || owner === undefined || owner < 0) return NEUTRAL;
  return COLORS[owner] || NEUTRAL;
}

function computeSeries() {
  const s = { ships: [], production: [], planets: [], shipDelta: [] };
  for (let p = 0; p < N_AGENTS; p++) {
    s.ships.push([]); s.production.push([]); s.planets.push([]);
  }
  for (const f of frames) {
    const ships = new Array(N_AGENTS).fill(0);
    const prod = new Array(N_AGENTS).fill(0);
    const cnt = new Array(N_AGENTS).fill(0);
    for (const pl of f.planets) {
      const owner = pl[1];
      if (owner !== null && owner >= 0 && owner < N_AGENTS) {
        ships[owner] += pl[5];
        prod[owner] += pl[6];
        cnt[owner] += 1;
      }
    }
    for (const fl of f.fleets) {
      const owner = fl[1];
      if (owner !== null && owner >= 0 && owner < N_AGENTS) ships[owner] += fl[6];
    }
    for (let p = 0; p < N_AGENTS; p++) {
      s.ships[p].push(ships[p]);
      s.production[p].push(prod[p]);
      s.planets[p].push(cnt[p]);
    }
    s.shipDelta.push(0);  // filled in below
  }
  // Per-round ship-gain delta: (p0 gain - p1 gain) for each tick.
  for (let i = 0; i < s.shipDelta.length; i++) {
    if (i === 0 || N_AGENTS < 2) {
      s.shipDelta[i] = 0;
    } else {
      const p0Gain = s.ships[0][i] - s.ships[0][i - 1];
      const p1Gain = s.ships[1][i] - s.ships[1][i - 1];
      s.shipDelta[i] = p0Gain - p1Gain;
    }
  }
  return s;
}

function loadMatch(idx) {
  currentMatchIdx = idx;
  const m = MATCHES[idx];
  frames = m.frames;
  debug = m.debug;
  N_AGENTS = m.n_agents;
  COLORS = m.colors;
  NAMES = m.names;
  series = computeSeries();
  cur = 0;
  maxViewed = 0;
  lastShakeStep = -1;
  regularShake = 0;
  playing = false;
  playBtn.textContent = 'Play';
  hoveredFleet = null; hoverPrediction = null;
  selectedFleetId = null; selectedPrediction = null;
  hoveredPlanetId = null; selectedPlanetId = null;
  scrub.max = Math.max(0, frames.length - 1);
  scrub.value = 0;
  // Show/hide delta panel based on agent count + update its title with names.
  if (N_AGENTS >= 2) {
    deltaPanel.style.display = '';
    document.getElementById('g3title').textContent =
      `Ship gain/tick (${NAMES[0]} - ${NAMES[1]})`;
  } else {
    deltaPanel.style.display = 'none';
  }
  // Update tab active styling
  for (const el of tabList.querySelectorAll('.tab')) {
    el.classList.toggle('active', parseInt(el.dataset.idx) === idx);
  }
  draw();
}

function buildTabs() {
  if (MATCHES.length <= 1) { tabList.classList.add('hidden'); return; }
  tabList.innerHTML = '';
  MATCHES.forEach((m, i) => {
    const div = document.createElement('div');
    div.className = 'tab' + (i === currentMatchIdx ? ' active' : '');
    div.dataset.idx = i;
    div.innerHTML = `<span class="label">${m.label}</span>
      <span class="res" data-idx="${i}"></span>`;
    div.onclick = () => loadMatch(i);
    tabList.appendChild(div);
  });
  updateTabResults();
}

// Reveal result line only for matches whose final step has been reached.
// We persist per-match "finished" flags so a different tab still shows the
// result of a previously-completed match.
const matchFinished = new Array(MATCHES.length).fill(false);
function updateTabResults() {
  for (let i = 0; i < MATCHES.length; i++) {
    const el = tabList.querySelector(`.res[data-idx="${i}"]`);
    if (!el) continue;
    if (matchFinished[i]) {
      const m = MATCHES[i];
      const resParts = m.names.map((n, p) => `${n}=${m.rewards[p] ?? '-'}`);
      el.textContent = resParts.join(' | ');
    } else {
      el.textContent = '— not viewed —';
    }
  }
}

function getSelectedFleet(frame) {
  if (selectedFleetId === null) return null;
  for (const fl of frame.fleets) if (fl[0] === selectedFleetId) return fl;
  return null;
}

function findPlanetAt(frame, mx, my) {
  for (const pl of frame.planets) {
    const dx = pl[2] - mx, dy = pl[3] - my;
    if (Math.hypot(dx, dy) <= pl[4] + 0.5) return pl;
  }
  return null;
}

function findPlanetById(frame, id) {
  for (const pl of frame.planets) if (pl[0] === id) return pl;
  return null;
}

function cometExpiry(frame, pid) {
  for (const g of (frame.comets || [])) {
    const ids = g.planet_ids || [];
    const idx = ids.indexOf(pid);
    if (idx >= 0) {
      const remaining = (g.paths[idx]?.length || 0) - g.path_index;
      return Math.max(0, remaining);
    }
  }
  return null;
}

function refreshSelectedPrediction() {
  const fl = getSelectedFleet(frames[cur]);
  selectedPrediction = fl ? predictFleet(fl, frames[cur]) : null;
}

function drawBoard() {
  const W = board.width, H = board.height;
  const scale = W / 100;
  bctx.setTransform(1, 0, 0, 1, 0, 0);
  bctx.fillStyle = '#070b1a';
  bctx.fillRect(0, 0, W, H);
  // (Non-cinema shake removed -- reserve the effect for cinema mode.)

  // Sun
  bctx.beginPath();
  bctx.arc(50 * scale, 50 * scale, 10 * scale, 0, Math.PI * 2);
  const grad = bctx.createRadialGradient(50*scale, 50*scale, 2*scale, 50*scale, 50*scale, 10*scale);
  grad.addColorStop(0, '#ffd28a'); grad.addColorStop(1, '#ff7a1a');
  bctx.fillStyle = grad; bctx.fill();

  // Rotation radius limit hint
  bctx.strokeStyle = '#1f2a4a';
  bctx.lineWidth = 1;
  bctx.beginPath();
  bctx.arc(50 * scale, 50 * scale, 50 * scale, 0, Math.PI * 2);
  bctx.stroke();

  // Trails
  if (trailsChk.checked) {
    const start = Math.max(0, cur - TRAIL_LEN);
    for (let s = start; s < cur; s++) {
      const alpha = (s - start) / TRAIL_LEN;
      for (const pl of frames[s].planets) {
        bctx.globalAlpha = alpha * 0.25;
        bctx.fillStyle = colorFor(pl[1]);
        bctx.beginPath();
        bctx.arc(pl[2] * scale, pl[3] * scale, Math.max(1, pl[4] * scale * 0.4), 0, Math.PI * 2);
        bctx.fill();
      }
    }
    bctx.globalAlpha = 1;
  }

  const f = frames[cur];

  // Orbit / comet path overlay for the hovered or selected planet.
  function drawOrbitFor(pid, style) {
    const planet = f.planets.find(p => p[0] === pid);
    if (!planet) return;
    bctx.strokeStyle = style.color;
    bctx.lineWidth = style.width;
    if (style.dash) bctx.setLineDash(style.dash); else bctx.setLineDash([]);
    if ((f.comet_planet_ids || []).includes(pid)) {
      // Comet: draw its precomputed path
      for (const g of (f.comets || [])) {
        const i = (g.planet_ids || []).indexOf(pid);
        if (i < 0) continue;
        const path = g.paths?.[i];
        if (!path || path.length < 2) return;
        bctx.beginPath();
        for (let j = 0; j < path.length; j++) {
          const x = path[j][0] * scale, y = path[j][1] * scale;
          if (j === 0) bctx.moveTo(x, y); else bctx.lineTo(x, y);
        }
        bctx.stroke();
        bctx.setLineDash([]);
        return;
      }
    } else {
      // Orbiting circle around the sun if it orbits
      const dx = planet[2] - 50, dy = planet[3] - 50;
      const orbR = Math.sqrt(dx * dx + dy * dy);
      if (orbR + planet[4] >= 50) { bctx.setLineDash([]); return; }
      bctx.beginPath();
      bctx.arc(50 * scale, 50 * scale, orbR * scale, 0, Math.PI * 2);
      bctx.stroke();
    }
    bctx.setLineDash([]);
  }
  if (selectedPlanetId !== null) {
    drawOrbitFor(selectedPlanetId, { color: '#ffd96688', width: 1.5, dash: null });
  }
  if (hoveredPlanetId !== null && hoveredPlanetId !== selectedPlanetId) {
    drawOrbitFor(hoveredPlanetId, { color: '#7cffc488', width: 1, dash: [4, 3] });
  }

  // Planet highlight rings (under the planet so they read as outlines)
  for (const pl of f.planets) {
    const id = pl[0], x = pl[2], y = pl[3], r = pl[4];
    if (id === selectedPlanetId) {
      bctx.strokeStyle = '#ffd966'; bctx.lineWidth = 2;
      bctx.beginPath(); bctx.arc(x*scale, y*scale, (r + 1.2) * scale, 0, Math.PI*2); bctx.stroke();
    } else if (id === hoveredPlanetId) {
      bctx.strokeStyle = '#7cffc4'; bctx.lineWidth = 1.5;
      bctx.setLineDash([3, 2]);
      bctx.beginPath(); bctx.arc(x*scale, y*scale, (r + 1.0) * scale, 0, Math.PI*2); bctx.stroke();
      bctx.setLineDash([]);
    }
  }

  // Planets
  for (const pl of f.planets) {
    const [id, owner, x, y, r, ships, prod] = pl;
    const isComet = f.comet_planet_ids && f.comet_planet_ids.includes(id);
    bctx.beginPath();
    bctx.arc(x * scale, y * scale, r * scale, 0, Math.PI * 2);
    bctx.fillStyle = colorFor(owner);
    bctx.globalAlpha = isComet ? 0.6 : 1;
    bctx.fill();
    bctx.globalAlpha = 1;
    if (isComet) {
      bctx.strokeStyle = '#fff'; bctx.lineWidth = 1;
      bctx.stroke();
    }
    // Ship count (centered on planet)
    bctx.fillStyle = '#fff';
    const fontSize = Math.max(9, r * scale * 0.7);
    bctx.font = `${fontSize}px sans-serif`;
    bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
    bctx.fillText(String(ships), x * scale, y * scale);
    // Production label (below the planet, just outside the radius)
    if (prodChk.checked) {
      const prodFont = Math.max(8, fontSize * 0.6);
      bctx.fillStyle = '#ffe28a';
      bctx.font = `${prodFont}px sans-serif`;
      bctx.textBaseline = 'top';
      bctx.fillText('+' + prod, x * scale, (y + r) * scale + 1);
    }
  }

  // Prediction overlays (selected pinned + hover preview)
  function drawPrediction(pred, fleet, opts) {
    if (!pred) return;
    const ok = pred.fate === 'planet';
    bctx.strokeStyle = ok ? opts.okColor : opts.badColor;
    bctx.lineWidth = opts.lineWidth;
    if (opts.dash) bctx.setLineDash(opts.dash); else bctx.setLineDash([]);
    bctx.beginPath();
    const t = pred.traj;
    bctx.moveTo(t[0][0] * scale, t[0][1] * scale);
    for (let i = 1; i < t.length; i++) bctx.lineTo(t[i][0] * scale, t[i][1] * scale);
    bctx.stroke();
    bctx.setLineDash([]);
    const [hx, hy] = pred.hitPos;
    bctx.beginPath();
    bctx.arc(hx * scale, hy * scale, 1.2 * scale, 0, Math.PI * 2);
    bctx.fillStyle = ok ? opts.okColor : opts.badColor;
    bctx.fill();
    if (pred.hitPlanet) {
      const hp = pred.hitPlanet;
      bctx.strokeStyle = opts.okColor;
      bctx.lineWidth = opts.lineWidth + 0.5;
      bctx.beginPath();
      bctx.arc(hp.x * scale, hp.y * scale, (hp.r + 0.5) * scale, 0, Math.PI * 2);
      bctx.stroke();
    }
    bctx.fillStyle = ok ? opts.okColor : opts.badColor;
    bctx.font = `${Math.max(10, 0.015 * W)}px sans-serif`;
    bctx.textAlign = 'left'; bctx.textBaseline = 'bottom';
    const label = ok ? `+${pred.hitStep}t` : `${pred.fate} +${pred.hitStep}t`;
    bctx.fillText(label, hx * scale + 3, hy * scale - 2);
    // Highlight ring on the fleet itself if pinned
    if (opts.ringOnFleet && fleet) {
      bctx.strokeStyle = opts.okColor;
      bctx.lineWidth = 1.5;
      bctx.beginPath();
      bctx.arc(fleet[2] * scale, fleet[3] * scale, 1.8 * scale, 0, Math.PI * 2);
      bctx.stroke();
    }
  }
  if (selectedPrediction) {
    drawPrediction(selectedPrediction, getSelectedFleet(f),
      { okColor: '#ffd966', badColor: '#ff5a5a', lineWidth: 2, dash: null, ringOnFleet: true });
  }
  if (hoverPrediction && (!selectedFleetId || (hoveredFleet && hoveredFleet[0] !== selectedFleetId))) {
    drawPrediction(hoverPrediction, hoveredFleet,
      { okColor: '#7cffc4', badColor: '#ff5a5a', lineWidth: 1.5, dash: [4, 3], ringOnFleet: false });
  }

  // Fleets
  for (const fl of f.fleets) {
    const [id, owner, x, y, angle, fromId, ships] = fl;
    const sizeMul = fleetSizeMultiplier(ships);
    bctx.save();
    bctx.translate(x * scale, y * scale);
    bctx.rotate(angle);
    bctx.fillStyle = colorFor(owner);
    bctx.beginPath();
    bctx.moveTo(1.2 * sizeMul * scale, 0);
    bctx.lineTo(-0.8 * sizeMul * scale, 0.7 * sizeMul * scale);
    bctx.lineTo(-0.8 * sizeMul * scale, -0.7 * sizeMul * scale);
    bctx.closePath();
    bctx.fill();
    bctx.restore();
    bctx.fillStyle = '#fff';
    bctx.font = `${Math.max(8, 0.012 * W)}px sans-serif`;
    bctx.textAlign = 'left'; bctx.textBaseline = 'middle';
    bctx.fillText(String(ships), (x + 1.3 * sizeMul) * scale, y * scale);
  }
}

function fleetSizeMultiplier(ships) {
  // Log scale -- 1 ship ~0.4x, 10 ~0.78x, 100 ~1.12x, 1000 ~1.46x. Smaller
  // overall than the earlier sqrt scaling so big fleets stay readable but
  // don't dominate the board.
  return Math.min(1.7, 0.4 + Math.log(Math.max(1, ships)) * 0.17);
}

function drawGraphs() {
  const stackedDatasets = [series.ships, series.production, series.planets];
  // Only plot up to the furthest step the user has actually viewed (anti-spoiler).
  // The x-axis spans 0..viewedT, growing as the user scrubs/plays forward.
  const viewedT = Math.max(1, maxViewed + 1);  // inclusive count
  const T = frames.length;
  // Standard panels 0..2: positive-only, multi-series.
  for (let panel = 0; panel < 3; panel++) {
    const ctx = graphCtxs[panel];
    const canvas = graphCanvases[panel];
    const W = canvas.width, H = canvas.height;
    const padL = 32, padR = 8, padT = 8, padB = 20;
    ctx.fillStyle = '#0a0f22';
    ctx.fillRect(0, 0, W, H);
    const left = padL, right = W - padR, top = padT, bot = H - padB;
    const innerW = right - left, innerH = bot - top;
    let maxV = 1;
    for (const arr of stackedDatasets[panel]) {
      for (let i = 0; i < viewedT; i++) if (arr[i] > maxV) maxV = arr[i];
    }
    ctx.strokeStyle = '#1f2a4a'; ctx.lineWidth = 1;
    ctx.fillStyle = '#8a96b8'; ctx.font = '10px sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let g = 0; g <= 3; g++) {
      const y = bot - (g / 3) * innerH;
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillText(Math.round((g / 3) * maxV), left - 4, y);
    }
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (let g = 0; g <= 4; g++) {
      const x = left + (g / 4) * innerW;
      const step = Math.round((g / 4) * Math.max(0, viewedT - 1));
      ctx.fillText(step, x, bot + 4);
    }
    for (let p = 0; p < N_AGENTS; p++) {
      const arr = stackedDatasets[panel][p];
      ctx.strokeStyle = COLORS[p]; ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (let i = 0; i < viewedT; i++) {
        const x = left + (i / Math.max(1, viewedT - 1)) * innerW;
        const y = bot - (arr[i] / maxV) * innerH;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    const px = left + (cur / Math.max(1, viewedT - 1)) * innerW;
    ctx.strokeStyle = '#ffffff66'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bot); ctx.stroke();
  }
  // Panel 3: ship delta (centered axis, positive in p0 color, negative in p1)
  if (N_AGENTS < 2) return;
  const ctx = graphCtxs[3];
  const canvas = graphCanvases[3];
  const W = canvas.width, H = canvas.height;
  const padL = 36, padR = 8, padT = 8, padB = 20;
  ctx.fillStyle = '#0a0f22';
  ctx.fillRect(0, 0, W, H);
  const left = padL, right = W - padR, top = padT, bot = H - padB;
  const innerW = right - left, innerH = bot - top;
  let maxAbs = 1;
  for (let i = 0; i < viewedT; i++) {
    const v = series.shipDelta[i];
    if (Math.abs(v) > maxAbs) maxAbs = Math.abs(v);
  }
  const yOf = v => top + innerH/2 - (v / maxAbs) * (innerH/2);
  const zeroY = yOf(0);
  // gridlines at -maxAbs, -maxAbs/2, 0, +maxAbs/2, +maxAbs
  ctx.strokeStyle = '#1f2a4a'; ctx.lineWidth = 1;
  ctx.fillStyle = '#8a96b8'; ctx.font = '10px sans-serif';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (const v of [maxAbs, maxAbs/2, 0, -maxAbs/2, -maxAbs]) {
    const y = yOf(v);
    ctx.strokeStyle = v === 0 ? '#3a4680' : '#1f2a4a';
    ctx.lineWidth = v === 0 ? 1.2 : 1;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillStyle = '#8a96b8';
    ctx.fillText((v > 0 ? '+' : '') + Math.round(v), left - 4, y);
  }
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  for (let g = 0; g <= 4; g++) {
    const x = left + (g / 4) * innerW;
    const step = Math.round((g / 4) * Math.max(0, viewedT - 1));
    ctx.fillText(step, x, bot + 4);
  }
  const drawFilled = (sign) => {
    const color = sign > 0 ? COLORS[0] : COLORS[1];
    ctx.fillStyle = color + '55';
    ctx.beginPath();
    ctx.moveTo(left, zeroY);
    for (let i = 0; i < viewedT; i++) {
      const v = series.shipDelta[i];
      const x = left + (i / Math.max(1, viewedT - 1)) * innerW;
      const y = sign > 0 ? yOf(Math.max(0, v)) : yOf(Math.min(0, v));
      ctx.lineTo(x, y);
    }
    ctx.lineTo(left + ((viewedT - 1) / Math.max(1, viewedT - 1)) * innerW, zeroY);
    ctx.closePath();
    ctx.fill();
  };
  drawFilled(1);
  drawFilled(-1);
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < viewedT; i++) {
    const x = left + (i / Math.max(1, viewedT - 1)) * innerW;
    const y = yOf(series.shipDelta[i]);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  const px = left + (cur / Math.max(1, viewedT - 1)) * innerW;
  ctx.strokeStyle = '#ffffff66'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bot); ctx.stroke();
  // current-value label
  const curVal = series.shipDelta[cur];
  ctx.fillStyle = curVal >= 0 ? COLORS[0] : COLORS[1];
  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  ctx.font = 'bold 11px sans-serif';
  ctx.fillText((curVal >= 0 ? '+' : '') + curVal, left + 4, top);
}

function drawSide() {
  const f = frames[cur];
  let html = `<h3>Step ${cur} / ${frames.length - 1}</h3>`;

  // Planet info: prefer pinned selection, fall back to hover.
  const infoPid = (selectedPlanetId !== null) ? selectedPlanetId : hoveredPlanetId;
  if (infoPid !== null) {
    const pl = findPlanetById(f, infoPid);
    if (pl) {
      const [id, owner, x, y, r, ships, prod] = pl;
      const ownerName = (owner >= 0 && owner < N_AGENTS) ? NAMES[owner] : 'neutral';
      const ownerColor = (owner >= 0 && owner < N_AGENTS) ? COLORS[owner] : '#888';
      const dx = x - 50, dy = y - 50;
      const dist = Math.hypot(dx, dy);
      const orbits = dist + r < 50;
      const isComet = (f.comet_planet_ids || []).includes(id);
      const expires = isComet ? cometExpiry(f, id) : null;
      const pinned = selectedPlanetId !== null;
      html += `<div style="margin-bottom:14px;padding:8px;border-radius:6px;border:1px solid ${pinned ? '#ffd966' : '#2a3560'}">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <b>Planet ${id}</b>
          <span class="pill" style="background:${ownerColor};color:#000;font-size:10px">${ownerName}</span>
        </div>
        <div class="stat-row"><span class="lbl">Ships</span><b>${ships}</b></div>
        <div class="stat-row"><span class="lbl">Production</span><b style="color:#ffe28a">+${prod}/turn</b></div>
        <div class="stat-row"><span class="lbl">Radius</span><span>${r.toFixed(2)}</span></div>
        <div class="stat-row"><span class="lbl">Position</span><span>${x.toFixed(1)}, ${y.toFixed(1)}</span></div>
        <div class="stat-row"><span class="lbl">Type</span><span>${isComet ? 'comet' : (orbits ? 'orbiting' : 'static')}</span></div>
        ${isComet && expires !== null ? `<div class="stat-row"><span class="lbl">Expires in</span><span>${expires} ticks</span></div>` : ''}
      </div>`;
    }
  }

  const reachedEnd = maxViewed >= frames.length - 1;
  for (let p = 0; p < N_AGENTS; p++) {
    const ships = series.ships[p][cur];
    const prod = series.production[p][cur];
    const cnt = series.planets[p][cur];
    const last = frames[frames.length - 1];
    const reward = reachedEnd ? last.rewards[p] : null;
    const status = reachedEnd ? last.statuses[p] : '';
    const statusLine = reachedEnd
      ? `${status || ''} reward=${reward ?? '-'}`
      : '';
    html += `<div style="margin-bottom:10px">
      <span class="pill" style="background:${COLORS[p]};color:#000">${NAMES[p] || ('Player ' + p)}</span>
      <span style="color:#8a96b8;font-size:11px">${statusLine}</span>
      <div class="stat-row"><span class="lbl">Ships</span><b>${ships}</b></div>
      <div class="stat-row"><span class="lbl">Production</span><b>${prod}</b></div>
      <div class="stat-row"><span class="lbl">Planets</span><b>${cnt}</b></div>
      <div class="stat-row"><span class="lbl">Actions this turn</span><b>${(f.actions[p] || []).length}</b></div>`;
    const d = debug && debug[cur] && debug[cur][p];
    if (d) {
      html += `<pre>${JSON.stringify(d, null, 1)}</pre>`;
    }
    html += `</div>`;
  }
  sideContent.innerHTML = html;
}

function draw() {
  if (cur > maxViewed) maxViewed = cur;
  if (maxViewed >= frames.length - 1 && !matchFinished[currentMatchIdx]) {
    matchFinished[currentMatchIdx] = true;
    updateTabResults();
  }
  // (Non-cinema shake detection removed -- effect kept exclusive to cinema.)
  lastShakeStep = cur;
  refreshSelectedPrediction();
  drawBoard();
  drawGraphs();
  drawSide();
  stepLbl.textContent = `Step ${cur} / ${frames.length - 1}`;
  scrub.value = cur;
}

function tick(t) {
  if (!playing) return;
  const dt = t - lastT;
  const speed = parseFloat(speedSel.value);
  const stepMs = 200 / speed;
  if (dt >= stepMs) {
    lastT = t;
    cur = Math.min(cur + 1, frames.length - 1);
    if (cur >= frames.length - 1) playing = false, playBtn.textContent = 'Play';
    draw();
  }
  requestAnimationFrame(tick);
}

playBtn.onclick = () => {
  if (cur >= frames.length - 1) cur = 0;
  playing = !playing;
  playBtn.textContent = playing ? 'Pause' : 'Play';
  if (playing) { lastT = performance.now(); requestAnimationFrame(tick); }
};
document.getElementById('prevBtn').onclick = () => { cur = Math.max(0, cur - 1); draw(); };
document.getElementById('nextBtn').onclick = () => { cur = Math.min(frames.length - 1, cur + 1); draw(); };
scrub.oninput = () => { if (cinemaMode) return; cur = parseInt(scrub.value); draw(); };
trailsChk.onchange = () => { if (!cinemaMode) draw(); };
prodChk.onchange = () => { if (!cinemaMode) draw(); };

function boardCoords(e) {
  const rect = board.getBoundingClientRect();
  const scale = board.width / 100;
  return [
    (e.clientX - rect.left) * (board.width / rect.width) / scale,
    (e.clientY - rect.top) * (board.height / rect.height) / scale,
  ];
}

function pickFleet(frame, mx, my, maxD = 2.5) {
  let best = null, bestD = maxD;
  for (const fl of frame.fleets) {
    const d = Math.hypot(fl[2] - mx, fl[3] - my);
    if (d < bestD) { bestD = d; best = fl; }
  }
  return best;
}

board.addEventListener('mousemove', (e) => {
  if (cinemaMode) return;
  const [mx, my] = boardCoords(e);
  const f = frames[cur];
  const fleet = pickFleet(f, mx, my);
  const planet = fleet ? null : findPlanetAt(f, mx, my);
  const newPid = planet ? planet[0] : null;
  const changed = (fleet !== hoveredFleet) || (newPid !== hoveredPlanetId);
  if (changed) {
    hoveredFleet = fleet;
    hoverPrediction = fleet ? predictFleet(fleet, f) : null;
    hoveredPlanetId = newPid;
    draw();
  }
});
board.addEventListener('mouseleave', () => {
  if (cinemaMode) return;
  if (hoveredFleet || hoveredPlanetId !== null) {
    hoveredFleet = null; hoverPrediction = null; hoveredPlanetId = null;
    draw();
  }
});
board.addEventListener('click', (e) => {
  if (cinemaMode) return;
  const [mx, my] = boardCoords(e);
  const f = frames[cur];
  const fleet = pickFleet(f, mx, my);
  if (fleet) {
    selectedFleetId = fleet[0] === selectedFleetId ? null : fleet[0];
    selectedPlanetId = null;
  } else {
    const planet = findPlanetAt(f, mx, my);
    if (planet) {
      selectedPlanetId = planet[0] === selectedPlanetId ? null : planet[0];
      selectedFleetId = null;
    } else {
      selectedFleetId = null;
      selectedPlanetId = null;
    }
  }
  draw();
});

function bumpSpeed(dir) {
  const opts = Array.from(speedSel.options).map(o => parseFloat(o.value));
  const idx = opts.indexOf(parseFloat(speedSel.value));
  const next = Math.max(0, Math.min(opts.length - 1, idx + dir));
  speedSel.value = String(opts[next]);
}

window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (cinemaMode) return;  // cinema runs its own loop; don't fight it
  if (playing && (e.key === 'ArrowLeft' || e.key === 'ArrowRight' ||
                  e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
    const dir = (e.key === 'ArrowRight' || e.key === 'ArrowUp') ? 1 : -1;
    bumpSpeed(dir);
    e.preventDefault();
    return;
  }
  if (e.key === 'ArrowLeft')      { cur = Math.max(0, cur - (e.shiftKey ? 10 : 1)); draw(); e.preventDefault(); }
  else if (e.key === 'ArrowRight') { cur = Math.min(frames.length - 1, cur + (e.shiftKey ? 10 : 1)); draw(); e.preventDefault(); }
  else if (e.key === 'ArrowUp')    { bumpSpeed(1); e.preventDefault(); }
  else if (e.key === 'ArrowDown')  { bumpSpeed(-1); e.preventDefault(); }
  else if (e.key === 'Home')       { cur = 0; draw(); e.preventDefault(); }
  else if (e.key === 'End')        { cur = frames.length - 1; draw(); e.preventDefault(); }
  else if (e.key === ' ')          { playBtn.click(); e.preventDefault(); }
});

// ============================================================
// CINEMA MODE
// ============================================================
const cinemaBtn = document.getElementById('cinemaBtn');
let cinemaMode = false;
let cinemaState = null;   // built per-match in loadMatch when cinema is on
let cinemaRAF = null;
const PI2 = Math.PI * 2;

// --- Event detection: scan frames once, emit prioritized events ---
function detectEvents(frames, series, N_AGENTS, NAMES) {
  const events = [];
  const T = frames.length;
  // Build per-step planet ownership map
  for (let t = 1; t < T; t++) {
    const prev = frames[t-1], cur = frames[t];
    const prevOwners = {};
    for (const p of prev.planets) prevOwners[p[0]] = { owner: p[1], ships: p[5], prod: p[6] };
    // 1) Planet flips
    for (const p of cur.planets) {
      const pid = p[0]; const o = p[1];
      const before = prevOwners[pid];
      if (before && before.owner !== o) {
        // Skip captures *from* neutral -- only highlight player-on-player flips.
        if (before.owner < 0) continue;
        const impactShips = Math.abs(before.ships - p[5]);
        // Did this capture *end* the game? (i.e. no further flips for the
        // loser, and they own no planets in the final frame)
        const finalPlanets = frames[frames.length - 1].planets;
        const loserHasPlanetsAtEnd =
          finalPlanets.some(pp => pp[1] === before.owner);
        const isGameEnder = !loserHasPlanetsAtEnd && t >= frames.length - 6;
        // 4p player-elimination: loser has no fleets and no planets *now* but
        // the game keeps going (other players still alive at end).
        const loserGoneNow = before.owner >= 0 &&
          !cur.fleets.some(fl => fl[1] === before.owner) &&
          !cur.planets.some(pp => pp[1] === before.owner);
        const is4pElim = !isGameEnder && N_AGENTS >= 3 && loserGoneNow;
        let evType, evImp;
        if (isGameEnder)  { evType = 'final_capture';     evImp = 999; }
        else if (is4pElim){ evType = 'player_eliminated'; evImp = 600; }
        else              { evType = 'flip';
                            evImp = 60 + p[6] * 15 + Math.min(impactShips, 50); }
        events.push({
          t, type: evType,
          pid, x: p[2], y: p[3], r: p[4],
          fromOwner: before.owner, toOwner: o, prod: p[6],
          ships: p[5],
          eliminationMode: is4pElim ? 'captured' : null,
          importance: evImp,
        });
      }
    }
    // 2) Fleet impacts: fleets present at t-1 not present at t -> died
    const curFleetIds = new Set(cur.fleets.map(f => f[0]));
    const finalFrame = frames[frames.length - 1];
    for (const f of prev.fleets) {
      if (curFleetIds.has(f[0])) continue;
      // Project the fleet forward one tick to figure out *why* it died.
      const speed = fleetSpeed(f[6]);
      const vx = Math.cos(f[4]) * speed;
      const vy = Math.sin(f[4]) * speed;
      const nextX = f[2] + vx;
      const nextY = f[3] + vy;
      const wentOOB = nextX < 0 || nextX > 100 || nextY < 0 || nextY > 100;
      // Nearest planet / sun for non-OOB classification.
      let near = null, nearD = 1e9;
      for (const p of cur.planets) {
        const d = Math.hypot(p[2] - f[2], p[3] - f[3]);
        if (d < nearD) { nearD = d; near = p; }
      }
      const hitSun = Math.hypot(f[2] - 50, f[3] - 50) < 14;
      const targetIsComet = near && (cur.comet_planet_ids || []).includes(near[0]);

      // OOB elimination -- fleet's owner has nothing left after this step
      // and its projected next position is off the board. 2p endgame fires
      // 'edge_out'; 4p mid-game elim fires 'player_eliminated' (mode='oob').
      if (wentOOB) {
        const owner = f[1];
        const ownerGoneNow = owner >= 0 &&
          !cur.fleets.some(fl => fl[1] === owner) &&
          !cur.planets.some(p => p[1] === owner);
        const ownerGoneAtEnd = owner >= 0 &&
          !finalFrame.fleets.some(fl => fl[1] === owner) &&
          !finalFrame.planets.some(p => p[1] === owner);
        if (ownerGoneNow && ownerGoneAtEnd) {
          // Solve fleet line vs the [0,100]^2 boundary -- where does it cross?
          let aCross = 1.0;
          if (vx > 1e-6) aCross = Math.min(aCross, (100 - f[2]) / vx);
          else if (vx < -1e-6) aCross = Math.min(aCross, -f[2] / vx);
          if (vy > 1e-6) aCross = Math.min(aCross, (100 - f[3]) / vy);
          else if (vy < -1e-6) aCross = Math.min(aCross, -f[3] / vy);
          aCross = Math.max(0, Math.min(1, aCross));
          const exitX = f[2] + vx * aCross;
          const exitY = f[3] + vy * aCross;
          // Game-ender if it's last-few ticks of the match.
          const isGameEnder2p = N_AGENTS === 2 && t >= frames.length - 6;
          if (isGameEnder2p) {
            events.push({
              t, type: 'edge_out',
              x: exitX, y: exitY,
              fleetX: f[2], fleetY: f[3],
              angle: f[4], ships: f[6], owner,
              importance: 999,
            });
            continue;
          } else if (N_AGENTS >= 3) {
            events.push({
              t, type: 'player_eliminated',
              x: exitX, y: exitY,
              fleetX: f[2], fleetY: f[3],
              angle: f[4], ships: f[6], owner,
              eliminationMode: 'oob',
              importance: 600,
            });
            continue;
          }
        }
      }

      // Failed assault: fleet hit an enemy planet, didn't capture it, AND
      // the fleet's owner has nothing left afterward. Either game-ending (2p
      // last assault) or 4p player elimination. Override the normal impact
      // classification so we can show the "REPELLED" treatment.
      if (!wentOOB && !hitSun && !targetIsComet && near) {
        const owner = f[1];
        const prevNear = prev.planets.find(pp => pp[0] === near[0]);
        // Hostile target both before and after this step (didn't flip).
        if (owner >= 0 && prevNear && prevNear[1] !== owner && near[1] !== owner) {
          const ownerGoneNow =
            !cur.fleets.some(fl => fl[1] === owner) &&
            !cur.planets.some(p => p[1] === owner);
          const ownerGoneAtEnd =
            !finalFrame.fleets.some(fl => fl[1] === owner) &&
            !finalFrame.planets.some(p => p[1] === owner);
          if (ownerGoneNow && ownerGoneAtEnd) {
            // Game-ender (2p) if only one other player remains alive at end.
            let activeAtEnd = 0;
            for (let pid = 0; pid < N_AGENTS; pid++) {
              if (finalFrame.fleets.some(fl => fl[1] === pid) ||
                  finalFrame.planets.some(p => p[1] === pid)) activeAtEnd++;
            }
            const isGameEnder = activeAtEnd <= 1;
            events.push({
              t, type: 'failed_assault',
              x: f[2], y: f[3],
              targetX: near[2], targetY: near[3], targetPid: near[0],
              owner, ships: f[6], targetOwner: near[1],
              gameEnder: isGameEnder,
              importance: 999,
            });
            continue;
          }
        }
      }

      const isClutch = !hitSun && !targetIsComet && f[6] >= 30;
      events.push({
        t, type: hitSun ? 'sun_death' : (targetIsComet ? 'comet_sweep' : 'impact'),
        x: f[2], y: f[3],
        targetX: near ? near[2] : f[2], targetY: near ? near[3] : f[3],
        owner: f[1], ships: f[6], targetPid: near ? near[0] : null,
        clutch: isClutch,
        importance: hitSun ? 25
                   : (targetIsComet ? 70
                   : (isClutch ? 90 + Math.min(f[6], 100) : 15 + Math.min(f[6], 80))),
      });
    }
    // 3) Comet spawns: comet_planet_ids new ids vs previous
    const prevCometIds = new Set(prev.comet_planet_ids || []);
    for (const pid of (cur.comet_planet_ids || [])) {
      if (!prevCometIds.has(pid)) {
        const p = cur.planets.find(pp => pp[0] === pid);
        if (p) events.push({
          t, type: 'comet_spawn', pid, x: p[2], y: p[3], r: p[4],
          importance: 55,
        });
      }
    }
    // 4) Massive fleet dispatched: new fleet with > 50 ships
    const prevFleetIds = new Set(prev.fleets.map(f => f[0]));
    for (const f of cur.fleets) {
      if (prevFleetIds.has(f[0])) continue;
      if (f[6] >= 50) events.push({
        t, type: 'big_fleet', x: f[2], y: f[3], owner: f[1], ships: f[6],
        importance: 25 + Math.min(f[6], 200) / 2,
      });
    }
    // 5) Lead change: total ships between players. Promote to 'comeback' when
    // the new leader was trailing by a wide margin (>= 25 ships) somewhere in
    // the last ~80 steps -- that's a real reversal, not a hand-over-hand swap.
    if (N_AGENTS >= 2 && t >= 5) {
      const prevDiff = series.ships[0][t-1] - series.ships[1][t-1];
      const curDiff = series.ships[0][t] - series.ships[1][t];
      if (Math.sign(prevDiff) !== Math.sign(curDiff) && Math.abs(prevDiff) + Math.abs(curDiff) > 0) {
        const newLeader = curDiff > 0 ? 0 : 1;
        let deficit = 0;
        if (t >= 30) {
          const lookback = Math.min(80, t - 5);
          for (let s = t - 5; s >= t - lookback; s--) {
            const d = series.ships[0][s] - series.ships[1][s];
            const trailedBy = newLeader === 1 ? d : -d;
            if (trailedBy > deficit) deficit = trailedBy;
          }
        }
        if (deficit >= 25) {
          events.push({
            t, type: 'comeback', x: 50, y: 50,
            toPlayer: newLeader, deficit,
            importance: 200,
          });
        } else {
          events.push({
            t, type: 'lead_change', x: 50, y: 50,
            fromPlayer: prevDiff > 0 ? 0 : 1, toPlayer: newLeader,
            importance: 80,
          });
        }
      }
    }
  }
  return events;
}

// De-dupe and select events: per-location fatigue + global budget.
function selectEvents(rawEvents, totalSteps) {
  const sorted = rawEvents.slice().sort((a, b) => b.importance - a.importance);
  // Per-location fatigue: each (type, pid?) bucket gets at most K hits per N steps.
  const lastByKey = {};
  const FATIGUE_WINDOW = Math.max(30, Math.floor(totalSteps * 0.08));
  const PER_KEY_MAX = 2;
  const keyCount = {};
  const kept = [];
  // Aim for ~ 1 hyped event per ~20 steps of game length, capped between 5 and 30.
  const targetCount = Math.max(5, Math.min(30, Math.floor(totalSteps / 18)));
  for (const e of sorted) {
    if (kept.length >= targetCount) break;
    const key = `${e.type}:${e.pid ?? e.targetPid ?? Math.round(e.x/10) + ',' + Math.round(e.y/10)}`;
    const last = lastByKey[key];
    keyCount[key] = keyCount[key] || 0;
    if (last !== undefined && Math.abs(e.t - last) < FATIGUE_WINDOW) continue;
    if (keyCount[key] >= PER_KEY_MAX) continue;
    lastByKey[key] = e.t;
    keyCount[key]++;
    kept.push(e);
  }
  kept.sort((a, b) => a.t - b.t);
  return kept;
}

// Cluster selected events whose ticks are within K of each other into groups
// (size 2-4 will become split-screen multi-shots). Returns array of arrays.
function clusterEvents(selected, K = 5) {
  const clusters = [];
  let cur = [];
  for (const e of selected) {
    if (cur.length === 0 || e.t - cur[cur.length - 1].t <= K) cur.push(e);
    else { clusters.push(cur); cur = [e]; }
  }
  if (cur.length > 0) clusters.push(cur);
  return clusters;
}

// Positional pane layout. Returns array of {event, fracX, fracY, fracW, fracH,
// target:{cx,cy,zoom}}. fracs are 0..1 of viewport.
function computePaneLayoutFracs(events) {
  const n = events.length;
  const eventZoom = e => {
    if (e.type === 'final_capture') return 2.2;
    if (e.type === 'edge_out' || (e.type === 'player_eliminated' && e.eliminationMode === 'oob')) return 1.0;
    if (e.type === 'failed_assault') return 2.0;
    if (e.type === 'player_eliminated') return 1.9;
    if (e.type === 'lead_change' || e.type === 'comeback') return 1.0;
    if (e.type === 'comet_spawn') return 1.6;
    if (e.type === 'comet_sweep') return 2.2;
    if (e.type === 'big_fleet') return 1.7;
    if (e.clutch) return 2.4;
    return 1.8;
  };
  const makePane = (event, fx, fy, fw, fh) => ({
    event, fracX: fx, fracY: fy, fracW: fw, fracH: fh,
    target: { cx: event.x, cy: event.y, zoom: eventZoom(event) },
  });
  if (n === 2) {
    const dx = Math.abs(events[0].x - events[1].x);
    const dy = Math.abs(events[0].y - events[1].y);
    if (dx >= dy) {
      const s = events.slice().sort((a, b) => a.x - b.x);
      return [makePane(s[0], 0, 0, 0.5, 1), makePane(s[1], 0.5, 0, 0.5, 1)];
    } else {
      const s = events.slice().sort((a, b) => a.y - b.y);
      return [makePane(s[0], 0, 0, 1, 0.5), makePane(s[1], 0, 0.5, 1, 0.5)];
    }
  }
  if (n === 3) {
    const s = events.slice().sort((a, b) => a.x - b.x);
    return [
      makePane(s[0], 0,     0, 1/3, 1),
      makePane(s[1], 1/3,   0, 1/3, 1),
      makePane(s[2], 2/3,   0, 1/3, 1),
    ];
  }
  // n >= 4 — use top 4 by importance, assigned to quadrants by their position.
  const top4 = events.slice().sort((a, b) => b.importance - a.importance).slice(0, 4);
  const quads = {
    TL: { fx: 0,   fy: 0,   fw: 0.5, fh: 0.5 },
    TR: { fx: 0.5, fy: 0,   fw: 0.5, fh: 0.5 },
    BL: { fx: 0,   fy: 0.5, fw: 0.5, fh: 0.5 },
    BR: { fx: 0.5, fy: 0.5, fw: 0.5, fh: 0.5 },
  };
  const used = new Set();
  const out = [];
  for (const e of top4) {
    const want = (e.y < 50 ? 'T' : 'B') + (e.x < 50 ? 'L' : 'R');
    // Try the natural quadrant, then horizontal mirror, then vertical, then opposite.
    const fallbacks = [
      want,
      want[0] + (want[1] === 'L' ? 'R' : 'L'),
      (want[0] === 'T' ? 'B' : 'T') + want[1],
      (want[0] === 'T' ? 'B' : 'T') + (want[1] === 'L' ? 'R' : 'L'),
    ];
    let pickKey = want;
    for (const k of fallbacks) {
      if (!used.has(k)) { pickKey = k; break; }
    }
    used.add(pickKey);
    const q = quads[pickKey];
    out.push(makePane(e, q.fx, q.fy, q.fw, q.fh));
  }
  // Sort by reading order (top-to-bottom, left-to-right) so iteration is stable.
  out.sort((a, b) => a.fracY - b.fracY || a.fracX - b.fracX);
  return out;
}

// Resolve fractional panes into absolute pixel rects + ensure each has a cam.
function materializePanes(shot, W, H, WIDE_ZOOM) {
  return shot.panes.map((p, i) => {
    if (!shot._cams) shot._cams = shot.panes.map(() => ({
      cx: 50, cy: 50, zoom: WIDE_ZOOM,
      tcx: 50, tcy: 50, tzoom: WIDE_ZOOM,
    }));
    const c = shot._cams[i];
    // Targets re-applied every call so split-in / split-out can update them.
    c.tcx = p._curTarget ? p._curTarget.cx : p.target.cx;
    c.tcy = p._curTarget ? p._curTarget.cy : p.target.cy;
    c.tzoom = p._curTarget ? p._curTarget.zoom : p.target.zoom;
    return {
      x: Math.floor(p.fracX * W), y: Math.floor(p.fracY * H),
      w: Math.ceil(p.fracW * W),  h: Math.ceil(p.fracH * H),
      cam: c, event: p.event, paneIdx: i,
    };
  });
}

// Build a list of "shots" for the cinema reel.
function buildShots(selected, totalSteps) {
  const shots = [];
  const WIDE_ZOOM = 0.78;
  shots.push({ kind: 'title', startStep: 0, duration: 110, target: { cx: 50, cy: 50, zoom: WIDE_ZOOM } });
  // Ambient pace ramps from ~2x normal playback in the early game to ~4-5x in
  // the late game. Normal game playback is ~5 steps/sec (~0.083 steps/frame at
  // 60fps), so we go from ~0.16 -> ~0.40 steps/frame across the match.
  const RATE_EARLY = 0.16;
  const RATE_LATE = 0.40;
  const ambientRateAt = (step) => {
    const denom = Math.max(1, totalSteps - 1);
    const p = Math.max(0, Math.min(1, step / denom));
    return RATE_EARLY + (RATE_LATE - RATE_EARLY) * p;
  };
  const firstEventStep = selected.length > 0 ? selected[0].t : totalSteps - 1;
  const openingEnd = Math.min(60, Math.max(15, firstEventStep - 4));
  shots.push({
    kind: 'ambient', fromStep: 0, toStep: openingEnd,
    stepsPerFrameStart: ambientRateAt(0), stepsPerFrameEnd: ambientRateAt(openingEnd),
    rampSteps: openingEnd,
    target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
  });
  let lastEnd = openingEnd;
  // Walk selected events as clusters (events within 5 ticks merge into a
  // single split-screen multi-shot).
  const clusters = clusterEvents(selected, 5);
  for (const cluster of clusters) {
    const firstT = cluster[0].t;
    const lastT = cluster[cluster.length - 1].t;
    const gap = firstT - lastEnd;
    // Fast-forward fill: only if there's a big gap to skip.
    if (gap > 25) {
      shots.push({
        kind: 'fill', fromStep: lastEnd, toStep: Math.max(lastEnd + 1, firstT - 10),
        target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
      });
      lastEnd = Math.max(lastEnd + 1, firstT - 10);
    }
    // Ambient establishing shot: play normal-paced gameplay at wide view for a beat,
    // so the eye resets before zooming in.
    const ambientFromStep = lastEnd;
    const ambientToStep = Math.max(ambientFromStep + 1, firstT - 4);
    if (ambientToStep > ambientFromStep) {
      shots.push({
        kind: 'ambient', fromStep: ambientFromStep, toStep: ambientToStep,
        stepsPerFrameStart: ambientRateAt(ambientFromStep),
        stepsPerFrameEnd: ambientRateAt(ambientToStep),
        rampSteps: ambientToStep - ambientFromStep,
        target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
      });
      lastEnd = ambientToStep;
    }

    // ---- Multi-event cluster -> split-screen shot ----
    if (cluster.length >= 2) {
      // Up to 4 panes; pick top-N by importance, sort by t for trigger order.
      const useEvents = cluster.length > 4
        ? cluster.slice().sort((a, b) => b.importance - a.importance).slice(0, 4)
        : cluster.slice();
      useEvents.sort((a, b) => a.t - b.t);
      const minT = useEvents[0].t, maxT = useEvents[useEvents.length - 1].t;
      const panes = computePaneLayoutFracs(useEvents);
      const types = new Set(useEvents.map(e => e.type));
      const allSameType = types.size === 1;
      shots.push({
        kind: 'multi', events: useEvents, panes,
        allSameType,
        fromStep: Math.max(lastEnd + 1, minT - 2),
        toStep: maxT,
        splitInFrames: 10,
        slowmoStepsPerFrame: 0.07,
        holdFrames: 65,
        splitOutFrames: 10,
        target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
      });
      shots.push({
        kind: 'decompress', duration: 12,
        target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
      });
      lastEnd = maxT;
      continue;
    }

    // ---- Single-event cluster -> classic approach/slowmo/impact ----
    const e = cluster[0];
    // Approach: pan to event with zoom; longer so the camera glide is visible.
    const isFinal = e.type === 'final_capture';
    const isEdgeOut = e.type === 'edge_out';
    const isPlayerElim = e.type === 'player_eliminated';
    const isFailedAssault = e.type === 'failed_assault';
    const isElimOOB = isPlayerElim && e.eliminationMode === 'oob';
    // For OOB-style elims, frame wide and aim at the midpoint of fleet→exit
    // so the boundary is visible (same trick edge_out uses).
    const wideOOB = isEdgeOut || isElimOOB;
    const focusX = wideOOB ? (e.fleetX + e.x) * 0.5
                 : isFailedAssault ? ((e.targetX ?? e.x) + e.x) * 0.5
                 : e.x;
    const focusY = wideOOB ? (e.fleetY + e.y) * 0.5
                 : isFailedAssault ? ((e.targetY ?? e.y) + e.y) * 0.5
                 : e.y;
    const focusZoom =
      wideOOB                       ? 0.85 :
      isFinal                       ? 2.4 :
      isFailedAssault && e.gameEnder ? 2.3 :
      isFailedAssault               ? 2.0 :
      isPlayerElim                  ? 1.9 :
      e.type === 'lead_change'      ? 1.1 :
      e.type === 'comeback'         ? 1.0 :
      e.type === 'comet_spawn'      ? 1.7 :
      e.type === 'big_fleet'        ? 1.8 :
      e.type === 'comet_sweep'      ? 2.3 :
      e.clutch                      ? 2.6 :
      2.0;
    const isHeavyEvent = isFinal || isEdgeOut ||
                         (isFailedAssault && e.gameEnder) || isPlayerElim;
    shots.push({
      kind: 'approach', event: e,
      atStep: Math.max(lastEnd + 1, e.t - 3),
      target: { cx: focusX, cy: focusY, zoom: focusZoom },
      duration: isHeavyEvent ? 30 : 18,
    });
    shots.push({
      kind: 'slowmo', event: e,
      fromStep: Math.max(lastEnd + 1, e.t - 2), toStep: e.t,
      stepsPerFrame: isHeavyEvent ? 0.05 : 0.14,
      target: { cx: focusX, cy: focusY, zoom: focusZoom },
    });
    shots.push({
      kind: 'impact', event: e,
      atStep: e.t,
      holdFrames: isHeavyEvent ? 75 : 35,
      target: { cx: focusX, cy: focusY, zoom: focusZoom * (isFinal ? 1.15 : 1.0) },
    });
    // Decompress: pull back to wide before next event
    shots.push({
      kind: 'decompress', duration: 11,
      target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
    });
    lastEnd = e.t;
  }
  // Tail: real-time play-out of the rest at the late-game ramped pace.
  if (lastEnd < totalSteps - 1) {
    shots.push({
      kind: 'ambient', fromStep: lastEnd, toStep: totalSteps - 1,
      stepsPerFrameStart: ambientRateAt(lastEnd),
      stepsPerFrameEnd: ambientRateAt(totalSteps - 1),
      rampSteps: totalSteps - 1 - lastEnd,
      target: { cx: 50, cy: 50, zoom: WIDE_ZOOM },
    });
  }
  // Final card
  shots.push({ kind: 'final', startStep: totalSteps - 1, duration: 260,
    target: { cx: 50, cy: 50, zoom: 1.0 } });
  return shots;
}

function buildUltraData() {
  // Contested planets: record per-planet flip ticks so we can label them
  // only during the actual contested window (not for the whole match).
  const flipTicksByPid = {};
  for (let t = 1; t < frames.length; t++) {
    const prevOwner = {};
    for (const p of frames[t-1].planets) prevOwner[p[0]] = p[1];
    for (const p of frames[t].planets) {
      const o = p[1];
      if (prevOwner[p[0]] !== undefined && prevOwner[p[0]] !== o &&
          prevOwner[p[0]] >= 0 && o >= 0) {
        if (!flipTicksByPid[p[0]]) flipTicksByPid[p[0]] = [];
        flipTicksByPid[p[0]].push(t);
      }
    }
  }
  // A planet is "contested" between its 3rd flip and ~40 ticks after its
  // most recent flip in a chain of >=3 close flips.
  const contestedWindows = {};
  for (const [pid, ticks] of Object.entries(flipTicksByPid)) {
    if (ticks.length < 3) continue;
    // Find any run of >=3 flips within a ~40-tick window each.
    for (let i = 0; i <= ticks.length - 3; i++) {
      if (ticks[i + 2] - ticks[i] <= 80) {
        // Mark from ticks[i+2] to ticks[<last in chain>] + 40
        let endIdx = i + 2;
        while (endIdx + 1 < ticks.length && ticks[endIdx + 1] - ticks[endIdx] <= 40) {
          endIdx++;
        }
        contestedWindows[pid] = {
          startT: ticks[i + 2],
          endT: ticks[endIdx] + 40,
          // Sorted list of every flip tick (used to count up live in the
          // cinema instead of spoiling the final total).
          flipTicks: ticks.slice(0, endIdx + 1),
        };
        break;
      }
    }
  }
  // Per-step lookup of biggest fleet seen so far + capture streak runs.
  let biggest = 0;
  const biggestByStep = new Array(frames.length).fill(0);
  for (let t = 0; t < frames.length; t++) {
    for (const f of frames[t].fleets) if (f[6] > biggest) biggest = f[6];
    biggestByStep[t] = biggest;
  }
  // Streaks: detect consecutive flips by same player in a short window.
  const flips = [];
  for (let t = 1; t < frames.length; t++) {
    const prevOwner = {};
    for (const p of frames[t-1].planets) prevOwner[p[0]] = p[1];
    for (const p of frames[t].planets) {
      const before = prevOwner[p[0]];
      if (before !== undefined && before !== p[1] && before >= 0 && p[1] >= 0) {
        flips.push({ t, player: p[1], pid: p[0] });
      }
    }
  }
  // Walk flips, find runs of same player within 40 ticks. We also record the
  // running count per individual flip so the cinema can show a small "STREAK
  // ×N" tag on every capture in a run -- not just the 3/5/7 big-caption beats.
  const streakEvents = [];
  const flipRunCount = {};  // `${t}:${pid}` -> count within current run (1+)
  let runPlayer = -1, runStart = 0, runCount = 0, runLastT = -100;
  for (const fl of flips) {
    if (fl.player === runPlayer && fl.t - runLastT <= 40) {
      runCount++;
      runLastT = fl.t;
      if (runCount === 3 || runCount === 5 || runCount === 7) {
        streakEvents.push({ t: fl.t, player: fl.player, count: runCount });
      }
    } else {
      runPlayer = fl.player; runStart = fl.t; runCount = 1; runLastT = fl.t;
    }
    flipRunCount[`${fl.t}:${fl.pid}`] = runCount;
  }
  // Comet paths: pid -> path array (for trajectory preview)
  const cometPaths = {};
  for (const fr of frames) {
    for (const g of (fr.comets || [])) {
      for (let i = 0; i < g.planet_ids.length; i++) {
        if (!cometPaths[g.planet_ids[i]]) cometPaths[g.planet_ids[i]] = g.paths[i];
      }
    }
  }
  return {
    contestedWindows, biggestByStep, streakEvents, cometPaths, flipRunCount,
    triggeredStreaks: new Set(),
    triggeredBiggest: 0,
  };
}

function buildCinema() {
  const events = detectEvents(frames, series, N_AGENTS, NAMES);
  const selected = selectEvents(events, frames.length);
  const shots = buildShots(selected, frames.length);
  return {
    shots, shotIdx: 0, shotFrame: 0,
    stepFloat: 0, // fractional step index
    cam: { cx: 50, cy: 50, zoom: 1.0, tcx: 50, tcy: 50, tzoom: 1.0 },
    particles: [],
    flashes: [],
    captions: [],
    shake: 0, vignette: 0,
    triggeredEvents: new Set(),
    eventCount: selected.length,
    totalEvents: events.length,
    eventLog: [],         // recent triggered events for the hype feed
    smoothedShare: 0.5,   // smoothed p0-ship share for the dominance bar
    smoothedFlow: 0,      // smoothed per-tick ship-gain delta
    capturesByPlayer: new Array(N_AGENTS).fill(0),
    fleetsSentByPlayer: new Array(N_AGENTS).fill(0),
    biggestFleet: 0,
    // Ultra-mode extras
    ultra: buildUltraData(),
    fleetTrails: new Map(),   // fleet_id -> [{x,y,owner}, ...] last positions
    damageNumbers: [],        // floating numbers from planet ship-count changes
    streakTag: null,          // single running STREAK ×N tag that hops to the
                              // latest capture; count == current run length so
                              // it always matches the 3/5/7 big caption.
    barrierFlashes: [],       // localized force-field hits where fleets leave
                              // the [0,100]^2 map: {x, y, edge, age, life}
    lastSeenStepBoundary: -1, // for one-shot per-step transitions
    edgeFlashAlpha: 0,        // current opacity of the red map-edge border
    edgeFlashTarget: 0,       // target opacity (lerped toward in drawCinemaFrame)
  };
}

// --- Cinema rendering ---
function projectCam(x, y, cam, W, H) {
  // Game is rendered in a centered square region. gameScale = min(W,H)/100;
  // offsets center the 100x100 game inside the canvas.
  const gameScale = Math.min(W, H) / 100;
  const offsetX = (W - 100 * gameScale) / 2;
  const offsetY = (H - 100 * gameScale) / 2;
  const cx = (x - cam.cx) * cam.zoom + 50;
  const cy = (y - cam.cy) * cam.zoom + 50;
  return [offsetX + cx * gameScale, offsetY + cy * gameScale];
}

function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

function smoothCamera(cam) {
  // Slightly elastic camera movement.
  cam.cx += (cam.tcx - cam.cx) * 0.06;
  cam.cy += (cam.tcy - cam.cy) * 0.06;
  // Zoom-punch: when punchTtl > 0, use a much faster lerp so the snap-in
  // bumped zoom settles back over ~10-15 frames instead of glacial 0.05.
  const zoomLerp = (cam.punchTtl && cam.punchTtl > 0) ? 0.18 : 0.05;
  cam.zoom += (cam.tzoom - cam.zoom) * zoomLerp;
  if (cam.punchTtl) cam.punchTtl--;
}

function getInterpolatedPlanet(p, pPrev, alpha, av) {
  // For orbiting: interpolate angle. For static or unknown: just blend coords.
  const dx = p[2] - 50, dy = p[3] - 50;
  const orbiting = Math.sqrt(dx*dx + dy*dy) + p[4] < 50;
  if (!orbiting || alpha <= 0 || !pPrev) {
    if (!pPrev) return { x: p[2], y: p[3] };
    return { x: pPrev[2] + (p[2] - pPrev[2]) * alpha, y: pPrev[3] + (p[3] - pPrev[3]) * alpha };
  }
  const dxp = pPrev[2] - 50, dyp = pPrev[3] - 50;
  const r = Math.hypot(dxp, dyp);
  const a0 = Math.atan2(dyp, dxp);
  const a1 = Math.atan2(dy, dx);
  let da = a1 - a0;
  while (da > Math.PI) da -= PI2;
  while (da < -Math.PI) da += PI2;
  const a = a0 + da * alpha;
  return { x: 50 + r * Math.cos(a), y: 50 + r * Math.sin(a) };
}

function getInterpolatedFleet(f, fPrev, alpha) {
  if (!fPrev) return { x: f[2], y: f[3], angle: f[4], ships: f[6], owner: f[1] };
  return {
    x: fPrev[2] + (f[2] - fPrev[2]) * alpha,
    y: fPrev[3] + (f[3] - fPrev[3]) * alpha,
    angle: f[4], ships: f[6], owner: f[1],
  };
}

function spawnExplosion(state, x, y, color, count = 30, speed = 1.5) {
  for (let i = 0; i < count; i++) {
    const a = Math.random() * PI2;
    const s = (0.5 + Math.random()) * speed;
    state.particles.push({
      x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s,
      life: 30 + Math.random() * 30, age: 0, size: 0.4 + Math.random() * 0.8,
      color,
    });
  }
}

function spawnShockwave(state, x, y, color) {
  state.flashes.push({ x, y, age: 0, life: 50, color, radius: 0, maxRadius: 16 });
}

function spawnRing(state, x, y, color, maxRadius = 8, life = 30) {
  state.flashes.push({ x, y, age: 0, life, color, radius: 0, maxRadius });
}

function pushCaption(state, text, sub, color, life = 80, priority = 1) {
  // Only ever display one caption at a time -- new ones replace the previous
  // so simultaneous events don't stack into an unreadable wall of text.
  // BUT: a low-priority caption (e.g. STREAK ×3) must not stomp an active
  // high-priority one (FINAL BLOW / ELIMINATED / LAST STAND FAILS) that's
  // still in its hold phase. Equal/higher priority always wins.
  if (state.captions.length) {
    const cur = state.captions[0];
    const curPrio = cur.priority || 1;
    const stillHolding = cur.age < cur.life - 20;
    if (priority < curPrio && stillHolding) return;
  }
  state.captions = [{ text, sub, color, age: 0, life, priority }];
}

function triggerEventEffects(state, event) {
  const key = `${event.t}:${event.type}:${event.pid ?? event.targetPid ?? ''}`;
  if (state.triggeredEvents.has(key)) return;
  state.triggeredEvents.add(key);
  // Log for the hype panel feed
  let logText = '', logColor = '#fff';
  if (event.type === 'flip') {
    logText = `P${event.pid} ${NAMES[event.fromOwner] ?? '?'}→${NAMES[event.toOwner] ?? '?'}`;
    logColor = event.toOwner >= 0 ? COLORS[event.toOwner] : '#888';
    state.capturesByPlayer[event.toOwner] = (state.capturesByPlayer[event.toOwner] || 0) + 1;
  } else if (event.type === 'impact') {
    logText = `${event.ships}-ship impact`; logColor = event.owner >= 0 ? COLORS[event.owner] : '#fff';
  } else if (event.type === 'sun_death') {
    logText = `Sun claims ${event.ships}`; logColor = '#ffb84a';
  } else if (event.type === 'comet_sweep') {
    logText = `Comet sweeps ${event.ships}`; logColor = '#9ad7ff';
  } else if (event.type === 'comet_spawn') {
    logText = `Comet incoming`; logColor = '#9ad7ff';
  } else if (event.type === 'big_fleet') {
    logText = `${event.ships}-ship assault`;
    logColor = event.owner >= 0 ? COLORS[event.owner] : '#fff';
    if (event.ships > state.biggestFleet) state.biggestFleet = event.ships;
    if (event.owner >= 0) state.fleetsSentByPlayer[event.owner]++;
  } else if (event.type === 'lead_change') {
    logText = `Lead: ${NAMES[event.toPlayer]}`; logColor = COLORS[event.toPlayer];
  } else if (event.type === 'comeback') {
    logText = `Comeback: ${NAMES[event.toPlayer]}`; logColor = COLORS[event.toPlayer];
  } else if (event.type === 'player_eliminated') {
    logText = `${NAMES[event.owner] ?? '?'} eliminated`;
    logColor = event.owner >= 0 ? COLORS[event.owner] : '#888';
  } else if (event.type === 'failed_assault') {
    logText = `${NAMES[event.owner] ?? '?'} repelled`;
    logColor = event.owner >= 0 ? COLORS[event.owner] : '#888';
  }
  if (logText) {
    state.eventLog.unshift({ t: event.t, text: logText, color: logColor, age: 0 });
    if (state.eventLog.length > 6) state.eventLog.length = 6;
  }
  // Helper: bump shake but never lower it
  const bumpShake = v => { state.shake = Math.max(state.shake, v); };
  switch (event.type) {
    case 'flip': {
      const toCol = event.toOwner >= 0 ? COLORS[event.toOwner] : '#888';
      const fromName = event.fromOwner >= 0 ? NAMES[event.fromOwner] : 'neutral';
      const toName = event.toOwner >= 0 ? NAMES[event.toOwner] : 'neutral';
      spawnExplosion(state, event.x, event.y, toCol, 60, 2.5);
      spawnShockwave(state, event.x, event.y, toCol);
      pushCaption(state, `PLANET ${event.pid} CAPTURED`, `${fromName} → ${toName}`, toCol, 150);
      bumpShake(22);
      break;
    }
    case 'final_capture': {
      const toCol = event.toOwner >= 0 ? COLORS[event.toOwner] : '#fff';
      const winName = event.toOwner >= 0 ? NAMES[event.toOwner] : 'Winner';
      spawnExplosion(state, event.x, event.y, toCol, 180, 4.5);
      spawnExplosion(state, event.x, event.y, '#ffd966', 80, 3.0);
      spawnExplosion(state, event.x, event.y, '#ffffff', 50, 5.5);
      spawnRing(state, event.x, event.y, toCol, 14, 80);
      spawnRing(state, event.x, event.y, '#ffd966', 28, 110);
      spawnRing(state, event.x, event.y, '#ffffff', 48, 150);
      pushCaption(state, 'FINAL BLOW', `${winName} wins`, toCol, 280, 5);
      bumpShake(60);
      state.victoryFlash = { color: toCol, age: 0, life: 60 };
      break;
    }
    case 'edge_out': {
      // Last enemy ship flies off the edge of the map. Big red explosion
      // *at the boundary intersection*, multiple shockwaves in red and the
      // map-edge red glow snaps to peak brightness (then fades via lerp).
      const loserName = event.owner >= 0 ? NAMES[event.owner] : '';
      spawnExplosion(state, event.x, event.y, '#ff3a3a', 120, 4.0);
      spawnExplosion(state, event.x, event.y, '#ff8a4a', 60, 3.0);
      spawnExplosion(state, event.x, event.y, '#ffffff', 30, 5.0);
      spawnRing(state, event.x, event.y, '#ff5a5a', 12, 80);
      spawnRing(state, event.x, event.y, '#ff9a5a', 22, 110);
      pushCaption(state, 'INTO THE VOID', `${loserName} eliminated`, '#ff5a5a', 280, 5);
      bumpShake(55);
      // Brighten the border to max for the impact frame; fade out is
      // managed by edgeFlashTarget toggling later in the impact shot.
      state.edgeFlashAlpha = 1;
      state.edgeFlashTarget = 1;
      // Subtle red overlay for the screen flash
      state.victoryFlash = { color: '#ff3a3a', age: 0, life: 50 };
      break;
    }
    case 'impact': {
      const c = event.owner >= 0 ? COLORS[event.owner] : '#fff';
      if (event.clutch) {
        // Hero play: single fleet of 30+ ships connects. Larger burst,
        // dual-color ring, and a "CLUTCH HIT" caption.
        spawnExplosion(state, event.x, event.y, c, 90, 3.6);
        spawnExplosion(state, event.x, event.y, '#ffd966', 35, 2.4);
        spawnRing(state, event.x, event.y, c, 12, 60);
        spawnRing(state, event.x, event.y, '#ffd966', 18, 75);
        pushCaption(state, 'CLUTCH HIT',
          `${NAMES[event.owner] || 'fleet'} · ${event.ships} ships`, c, 140);
        bumpShake(28);
      } else {
        spawnExplosion(state, event.x, event.y, c, 30, 2.0);
        spawnRing(state, event.x, event.y, c, 6, 30);
        bumpShake(10);
      }
      break;
    }
    case 'sun_death': {
      spawnExplosion(state, event.x, event.y, '#ff7a1a', 40, 2.5);
      spawnRing(state, event.x, event.y, '#ffd28a', 10, 40);
      bumpShake(16);
      break;
    }
    case 'comet_sweep': {
      const c = event.owner >= 0 ? COLORS[event.owner] : '#fff';
      spawnExplosion(state, event.x, event.y, c, 50, 3);
      spawnExplosion(state, event.x, event.y, '#9ad7ff', 30, 2);
      pushCaption(state, 'COMET SWEEP', `${NAMES[event.owner] || 'fleet'} obliterated`, '#9ad7ff', 150);
      bumpShake(35);
      break;
    }
    case 'comet_spawn': {
      spawnRing(state, event.x, event.y, '#9ad7ff', 14, 50);
      pushCaption(state, 'INCOMING COMET', '', '#9ad7ff', 130);
      bumpShake(10);
      break;
    }
    case 'big_fleet': {
      const c = event.owner >= 0 ? COLORS[event.owner] : '#fff';
      pushCaption(state, `${event.ships} SHIP ASSAULT`, NAMES[event.owner] || '', c, 140);
      spawnRing(state, event.x, event.y, c, 5, 20);
      bumpShake(8);
      break;
    }
    case 'lead_change': {
      const c = COLORS[event.toPlayer];
      pushCaption(state, 'LEAD CHANGE', `${NAMES[event.toPlayer]} takes over`, c, 150);
      spawnRing(state, 50, 50, c, 22, 60);
      bumpShake(18);
      break;
    }
    case 'comeback': {
      const c = COLORS[event.toPlayer];
      pushCaption(state, 'COMEBACK',
        `${NAMES[event.toPlayer]} claws back from -${event.deficit}`, c, 200);
      // Triple-ring out from center sells the reversal.
      spawnRing(state, 50, 50, c, 18, 60);
      spawnRing(state, 50, 50, '#ffd966', 28, 80);
      spawnRing(state, 50, 50, c, 40, 110);
      bumpShake(34);
      // Subtle screen tint toward winner's color
      state.victoryFlash = { color: c, age: 0, life: 35 };
      break;
    }
    case 'player_eliminated': {
      // 4p mid-game elimination. Dim color = the eliminated player's color
      // (final blast in their own color), with a desaturating overlay.
      const lostName = event.owner >= 0 ? NAMES[event.owner] : '';
      const lostCol = event.owner >= 0 ? COLORS[event.owner] : '#888';
      const mode = event.eliminationMode;
      const sub = mode === 'oob' ? `${lostName} lost in the void`
                : mode === 'captured' ? `${lostName} loses last planet`
                : `${lostName} eliminated`;
      spawnExplosion(state, event.x, event.y, lostCol, 110, 3.8);
      spawnExplosion(state, event.x, event.y, '#ffffff', 25, 4.5);
      spawnRing(state, event.x, event.y, lostCol, 16, 80);
      spawnRing(state, event.x, event.y, '#cccccc', 26, 110);
      pushCaption(state, 'ELIMINATED', sub, lostCol, 200, 4);
      bumpShake(40);
      // Brief grey screen flash -- the player's gone.
      state.victoryFlash = { color: '#888888', age: 0, life: 30 };
      break;
    }
    case 'failed_assault': {
      // Last fleet hit a planet but couldn't take it. Visual: attacker bursts
      // *against* the planet's halo in the target owner's color, then a
      // smaller wash in the attacker's color showing they're gone.
      const atkCol = event.owner >= 0 ? COLORS[event.owner] : '#888';
      const defCol = event.targetOwner >= 0 ? COLORS[event.targetOwner] : '#888';
      const atkName = event.owner >= 0 ? NAMES[event.owner] : '';
      // Defender's halo "absorbs" the hit
      spawnExplosion(state, event.targetX || event.x, event.targetY || event.y,
        defCol, 60, 3.0);
      spawnRing(state, event.targetX || event.x, event.targetY || event.y,
        defCol, 14, 80);
      // Attacker's last gasp at the impact point
      spawnExplosion(state, event.x, event.y, atkCol, 50, 2.6);
      spawnRing(state, event.x, event.y, atkCol, 10, 60);
      const headline = event.gameEnder ? 'LAST STAND FAILS' : 'REPELLED';
      const sub = event.gameEnder
        ? `${atkName} can't break the line`
        : `${atkName}'s final assault breaks against ${NAMES[event.targetOwner] || 'the defender'}`;
      pushCaption(state, headline, sub, defCol, event.gameEnder ? 280 : 200, event.gameEnder ? 5 : 3);
      bumpShake(event.gameEnder ? 55 : 32);
      if (event.gameEnder) {
        state.victoryFlash = { color: defCol, age: 0, life: 50 };
      }
      break;
    }
  }
}

function drawPlanetTexture(ctx, sx, sy, sr, owner, isComet, prod) {
  const baseCol = (owner !== undefined && owner >= 0) ? COLORS[owner] : '#8a96b8';
  // Halo for owned planets
  if (owner >= 0 && owner < N_AGENTS) {
    const haloR = sr * 1.6;
    const g = ctx.createRadialGradient(sx, sy, sr * 0.9, sx, sy, haloR);
    g.addColorStop(0, baseCol + 'aa');
    g.addColorStop(1, baseCol + '00');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(sx, sy, haloR, 0, PI2);
    ctx.fill();
  }
  // Body radial gradient
  const grad = ctx.createRadialGradient(sx - sr * 0.4, sy - sr * 0.4, sr * 0.1, sx, sy, sr);
  grad.addColorStop(0, '#ffffffcc');
  grad.addColorStop(0.18, baseCol);
  grad.addColorStop(1, '#000');
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.arc(sx, sy, sr, 0, PI2); ctx.fill();
  // Banding
  ctx.save();
  ctx.beginPath(); ctx.arc(sx, sy, sr, 0, PI2); ctx.clip();
  ctx.globalAlpha = 0.18;
  for (let i = -2; i <= 2; i++) {
    ctx.fillStyle = i % 2 === 0 ? '#ffffff' : '#000000';
    ctx.fillRect(sx - sr, sy + i * sr * 0.3 - sr * 0.05, sr * 2, sr * 0.1);
  }
  ctx.restore();
  if (isComet) {
    ctx.strokeStyle = '#9ad7ff';
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(sx, sy, sr + 1, 0, PI2); ctx.stroke();
  }
}

function processStepTransition(t) {
  // Called once per crossed step boundary in ultra mode. Spawns launch puffs,
  // damage numbers, and triggers streak/biggest-fleet callouts.
  if (t < 1 || t >= frames.length) return;
  const prev = frames[t-1], cur = frames[t];
  // Force-field barrier flashes -- any fleet that died this step with its
  // projected next position off the [0,100]^2 board hit the edge. Spawn a
  // localized red glow at the exit point so the boundary feels solid.
  {
    const curFleetIds = new Set(cur.fleets.map(f => f[0]));
    for (const f of prev.fleets) {
      if (curFleetIds.has(f[0])) continue;
      const speed = fleetSpeed(f[6]);
      const vx = Math.cos(f[4]) * speed;
      const vy = Math.sin(f[4]) * speed;
      const nx = f[2] + vx, ny = f[3] + vy;
      if (nx < 0 || nx > 100 || ny < 0 || ny > 100) {
        // Where does the fleet line cross the boundary?
        let aCross = 1.0;
        if (vx > 1e-6) aCross = Math.min(aCross, (100 - f[2]) / vx);
        else if (vx < -1e-6) aCross = Math.min(aCross, -f[2] / vx);
        if (vy > 1e-6) aCross = Math.min(aCross, (100 - f[3]) / vy);
        else if (vy < -1e-6) aCross = Math.min(aCross, -f[3] / vy);
        aCross = Math.max(0, Math.min(1, aCross));
        const ex = f[2] + vx * aCross;
        const ey = f[3] + vy * aCross;
        // Snap to whichever edge is closest so the glow sits exactly on it.
        const dT = ey, dB = 100 - ey, dL = ex, dR = 100 - ex;
        const minD = Math.min(dT, dB, dL, dR);
        let edge = 'T', exX = ex, exY = ey;
        if (minD === dT)      { edge = 'T'; exY = 0; }
        else if (minD === dB) { edge = 'B'; exY = 100; }
        else if (minD === dL) { edge = 'L'; exX = 0; }
        else                  { edge = 'R'; exX = 100; }
        cinemaState.barrierFlashes.push({
          x: exX, y: exY, edge, age: 0, life: 55,
          ships: f[6], owner: f[1],
        });
        if (cinemaState.barrierFlashes.length > 12)
          cinemaState.barrierFlashes.splice(0, cinemaState.barrierFlashes.length - 12);
      }
    }
  }
  // Launch puffs for newly created fleets.
  const prevIds = new Set(prev.fleets.map(f => f[0]));
  for (const f of cur.fleets) {
    if (prevIds.has(f[0])) continue;
    const srcPid = f[5];
    const src = prev.planets.find(p => p[0] === srcPid) || cur.planets.find(p => p[0] === srcPid);
    if (src) {
      const c = f[1] >= 0 ? COLORS[f[1]] : '#fff';
      // Small directional puff in fleet's launch direction
      for (let i = 0; i < 14; i++) {
        const spread = (Math.random() - 0.5) * 0.8;
        const ang = f[4] + spread;
        const sp = 0.4 + Math.random() * 0.8;
        cinemaState.particles.push({
          x: src[2] + Math.cos(f[4]) * src[4] * 0.6,
          y: src[3] + Math.sin(f[4]) * src[4] * 0.6,
          vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
          life: 20 + Math.random() * 10, age: 0, size: 0.3 + Math.random() * 0.5,
          color: c,
        });
      }
    }
  }
  // Production-tick pulses: every owned, non-comet planet emits a faint
  // ring sized by its production. Drawn before damage / flip effects so big
  // explosions overdraw cleanly. Skipped during fast-forward (the whole
  // processStepTransition body already is) so we never accumulate them.
  const cometNow = new Set(cur.comet_planet_ids || []);
  for (const p of cur.planets) {
    if (p[1] < 0) continue;       // neutral
    if (cometNow.has(p[0])) continue; // comets don't produce
    if (p[6] <= 0) continue;      // zero-production
    const baseCol = COLORS[p[1]];
    cinemaState.flashes.push({
      x: p[2], y: p[3],
      age: 0, life: 18,
      // Append two-char hex alpha (~0.2) so the ring is subtle vs explosions.
      color: baseCol + '33',
      radius: 0,
      maxRadius: p[4] * (1.4 + Math.min(p[6], 6) * 0.18),
    });
  }
  // Damage numbers + capture-effect particles for every player-on-player flip
  // (regardless of whether the flip was selected for the cinema highlight reel).
  const prevById = {}; for (const p of prev.planets) prevById[p[0]] = p;
  for (const p of cur.planets) {
    const old = prevById[p[0]];
    if (!old) continue;
    const ownerChanged = old[1] !== p[1];
    const delta = p[5] - old[5];
    // Player-on-player flip: spawn a modest capture burst here. The selected
    // cinema-highlight flips also fire a larger burst from triggerEventEffects,
    // so selected events come out visually bigger -- which is the right
    // hierarchy. Skip captures from/to neutral (they're not interesting).
    if (ownerChanged && old[1] >= 0 && p[1] >= 0) {
      const toCol = COLORS[p[1]] || '#fff';
      spawnExplosion(cinemaState, p[2], p[3], toCol, 28, 2.0);
      spawnShockwave(cinemaState, p[2], p[3], toCol);
      cinemaState.shake = Math.max(cinemaState.shake, 14);
      // Streak tag: one running tag that hops to whichever planet was just
      // captured in the current run. rc==1 means a new run started (different
      // player or 40+ ticks since last flip) -- clear the tag.
      const rc = cinemaState.ultra && cinemaState.ultra.flipRunCount
        ? cinemaState.ultra.flipRunCount[`${t}:${p[0]}`] : null;
      if (rc && rc >= 2) {
        cinemaState.streakTag = {
          pid: p[0],
          text: `STREAK ×${rc}`,
          color: toCol,
          age: 0, life: 130,
        };
      } else if (rc === 1) {
        cinemaState.streakTag = null;
      }
    }
    // Damage-number text
    if (!ownerChanged && Math.abs(delta) <= old[6]) continue;
    if (delta === 0) continue;
    const sign = delta > 0 ? '+' : '−';
    const absD = Math.abs(delta);
    if (absD < 3 && !ownerChanged) continue;
    const c = delta > 0
      ? (p[1] >= 0 ? COLORS[p[1]] : '#fff')
      : (old[1] >= 0 ? COLORS[old[1]] : '#fff');
    cinemaState.damageNumbers.push({
      x: p[2], y: p[3] - p[4] - 0.5,
      text: `${sign}${absD}`,
      color: c,
      age: 0, life: 70, vy: -0.08,
    });
  }
  // Ultra-only callouts
  if (cinemaState.ultra) {
    // Streak callouts
    for (const s of cinemaState.ultra.streakEvents) {
      if (s.t === t && !cinemaState.ultra.triggeredStreaks.has(`${s.t}:${s.player}:${s.count}`)) {
        cinemaState.ultra.triggeredStreaks.add(`${s.t}:${s.player}:${s.count}`);
        const c = COLORS[s.player] || '#fff';
        pushCaption(cinemaState,
          `${s.count}-CAPTURE STREAK`,
          NAMES[s.player] || '', c, 130, 1);
      }
    }
    // Biggest-fleet callouts -- attach to the actual fleet as a floating
    // tag, not a full-screen caption.
    const big = cinemaState.ultra.biggestByStep[t];
    if (big > cinemaState.ultra.triggeredBiggest && big >= 30) {
      cinemaState.ultra.triggeredBiggest = big;
      const newFleet = cur.fleets.find(f => f[6] === big && !prevIds.has(f[0]));
      if (newFleet) {
        if (!cinemaState.recordFleetTags) cinemaState.recordFleetTags = [];
        cinemaState.recordFleetTags.push({
          fleetId: newFleet[0],
          text: `RECORD · ${big}`,
          color: newFleet[1] >= 0 ? COLORS[newFleet[1]] : '#fff',
          age: 0, life: 220,  // dies on its own after a while
        });
      }
    }
  }
}

// Advances all per-frame state mutations (particle aging, shake decay, caption
// aging, fleet-trail accumulation, etc.). Called once per frame, BEFORE the
// scene is drawn -- because in split-screen mode the scene draw runs once per
// pane and these mutations must not be re-applied per pane.
function tickWorldState(fa, fb, alpha) {
  // Shake decay
  if (cinemaState.shake > 0) {
    cinemaState.shake *= 0.92;
    if (cinemaState.shake < 0.4) cinemaState.shake = 0;
  }
  // Particle / flash rates -- slower aging during slowmo / impact so the
  // dramatic beat doesn't lose its embers.
  const curShot = cinemaState.shots[cinemaState.shotIdx];
  const slowShot = curShot && (curShot.kind === 'slowmo' || curShot.kind === 'impact' || curShot.kind === 'multi');
  const ageRate = slowShot ? 0.35 : 1.0;
  const motionRate = slowShot ? 0.5 : 1.0;
  // Particles
  for (let i = cinemaState.particles.length - 1; i >= 0; i--) {
    const p = cinemaState.particles[i];
    p.age += ageRate;
    p.x += p.vx * motionRate; p.y += p.vy * motionRate;
    p.vx *= 0.96; p.vy *= 0.96;
    if (p.age >= p.life) cinemaState.particles.splice(i, 1);
  }
  // Flashes (radius gets recomputed at draw time from age/life)
  for (let i = cinemaState.flashes.length - 1; i >= 0; i--) {
    const fl = cinemaState.flashes[i];
    fl.age += ageRate;
    if (fl.age >= fl.life) cinemaState.flashes.splice(i, 1);
  }
  // Damage numbers
  for (let i = cinemaState.damageNumbers.length - 1; i >= 0; i--) {
    const d = cinemaState.damageNumbers[i];
    d.age++;
    d.y += d.vy;
    if (d.age >= d.life) cinemaState.damageNumbers.splice(i, 1);
  }
  // Captions
  if (cinemaState.captions.length) {
    const c = cinemaState.captions[0];
    c.age++;
    if (c.age >= c.life) cinemaState.captions.length = 0;
  }
  // Per-pane captions (multi-shot, mixed-type case)
  if (cinemaState.paneCaptions) {
    for (let i = cinemaState.paneCaptions.length - 1; i >= 0; i--) {
      const pc = cinemaState.paneCaptions[i];
      pc.age++;
      if (pc.age >= pc.life) cinemaState.paneCaptions.splice(i, 1);
    }
  }
  // Victory flash
  if (cinemaState.victoryFlash) {
    cinemaState.victoryFlash.age++;
    if (cinemaState.victoryFlash.age >= cinemaState.victoryFlash.life)
      cinemaState.victoryFlash = null;
  }
  // Edge-flash alpha lerp
  if (cinemaState.edgeFlashAlpha === undefined) cinemaState.edgeFlashAlpha = 0;
  const eTarget = cinemaState.edgeFlashTarget || 0;
  cinemaState.edgeFlashAlpha += (eTarget - cinemaState.edgeFlashAlpha) * 0.06;
  // Record fleet tags
  if (cinemaState.recordFleetTags) {
    for (let i = cinemaState.recordFleetTags.length - 1; i >= 0; i--) {
      const tag = cinemaState.recordFleetTags[i];
      tag.age++;
      if (tag.age >= tag.life) cinemaState.recordFleetTags.splice(i, 1);
    }
  }
  // Streak tag
  if (cinemaState.streakTag) {
    cinemaState.streakTag.age++;
    if (cinemaState.streakTag.age >= cinemaState.streakTag.life)
      cinemaState.streakTag = null;
  }
  // Barrier flashes (localized force-field hits)
  if (cinemaState.barrierFlashes) {
    for (let i = cinemaState.barrierFlashes.length - 1; i >= 0; i--) {
      const bf = cinemaState.barrierFlashes[i];
      bf.age += ageRate;
      if (bf.age >= bf.life) cinemaState.barrierFlashes.splice(i, 1);
    }
  }
  // Fleet trail accumulation (once per frame, even in multi-pane mode)
  const fleetsA = {};
  for (const f of fa.fleets) fleetsA[f[0]] = f;
  for (const f of fb.fleets) {
    const prev = fleetsA[f[0]];
    let x, y, owner = f[1];
    if (prev) {
      x = prev[2] + (f[2] - prev[2]) * alpha;
      y = prev[3] + (f[3] - prev[3]) * alpha;
    } else { x = f[2]; y = f[3]; }
    let tr = cinemaState.fleetTrails.get(f[0]);
    if (!tr) { tr = []; cinemaState.fleetTrails.set(f[0], tr); }
    tr.push({ x, y, owner });
    if (tr.length > 12) tr.shift();
  }
  for (const [fid, tr] of cinemaState.fleetTrails) {
    const stillExists = fb.fleets.some(f => f[0] === fid);
    if (!stillExists) {
      if (tr.length > 0) tr.shift();
      if (tr.length === 0) cinemaState.fleetTrails.delete(fid);
    }
  }
}

function drawCinemaFrame() {
  if (!cinemaState) return;
  const W = board.width, H = board.height;
  // Process newly-crossed step boundaries for one-shot effects (ultra only).
  const newStep = Math.floor(cinemaState.stepFloat);
  if (newStep > cinemaState.lastSeenStepBoundary) {
    const curShot0 = cinemaState.shots[cinemaState.shotIdx];
    // Skip during fast-forward fills -- not worth the per-step work and the
    // accumulated particles tank framerate.
    if (!curShot0 || curShot0.kind !== 'fill') {
      for (let t = cinemaState.lastSeenStepBoundary + 1; t <= newStep; t++) {
        processStepTransition(t);
      }
    }
    cinemaState.lastSeenStepBoundary = newStep;
    // Bound particle/damage-number lifetime by trimming the buffers.
    if (cinemaState.particles.length > 250)
      cinemaState.particles.splice(0, cinemaState.particles.length - 250);
    if (cinemaState.damageNumbers.length > 40)
      cinemaState.damageNumbers.splice(0, cinemaState.damageNumbers.length - 40);
  }

  // Compute interpolation state once per frame (shared across all panes).
  const stepFloor = Math.max(0, Math.min(frames.length - 1, Math.floor(cinemaState.stepFloat)));
  const stepNext = Math.min(frames.length - 1, stepFloor + 1);
  const alpha = cinemaState.stepFloat - stepFloor;
  const fa = frames[stepFloor];
  const fb = frames[stepNext];
  const av = fa.angular_velocity || 0;

  // Tick all per-frame state mutations BEFORE the scene draw, so split-screen
  // doesn't tick them once per pane.
  tickWorldState(fa, fb, alpha);

  const sx = (cinemaState.shake > 0) ? (Math.random() - 0.5) * cinemaState.shake : 0;
  const sy = (cinemaState.shake > 0) ? (Math.random() - 0.5) * cinemaState.shake : 0;

  // Build the list of viewports to render. Single shot: one full-screen pane.
  // Multi shot: one viewport per event, positioned by computePaneLayoutFracs.
  const shotCur = cinemaState.shots[cinemaState.shotIdx];
  const panes = (shotCur && shotCur.kind === 'multi' && shotCur.panes)
    ? materializePanes(shotCur, W, H, 0.78)
    : [{ x: 0, y: 0, w: W, h: H, cam: cinemaState.cam }];
  for (const pane of panes) smoothCamera(pane.cam);

  // Re-declare shared interp tables once for the closure used by world draw.
  const planetsA = {};
  for (const p of fa.planets) planetsA[p[0]] = p;
  const planetsB = {};
  for (const p of fb.planets) planetsB[p[0]] = p;
  const fleetsA = {};
  for (const f of fa.fleets) fleetsA[f[0]] = f;
  const fbIds = new Set(fb.fleets.map(f => f[0]));

  // ---- Per-pane scene render ----
  for (let paneIdx = 0; paneIdx < panes.length; paneIdx++) {
    const pane = panes[paneIdx];
    const multi = panes.length > 1;
    if (multi) {
      bctx.save();
      bctx.beginPath();
      bctx.rect(pane.x, pane.y, pane.w, pane.h);
      bctx.clip();
      bctx.translate(pane.x, pane.y);
    }
    const W = pane.w, H = pane.h, cam = pane.cam;
    const gameScale = Math.min(W, H) / 100;
  // Background fills the whole viewport, not just the game region.
  const bg = bctx.createRadialGradient(W/2, H/2, 10, W/2, H/2, Math.max(W, H));
  bg.addColorStop(0, '#0a1230'); bg.addColorStop(1, '#02040c');
  bctx.fillStyle = bg;
  bctx.fillRect(0, 0, W, H);
  // Background nebula -- 5 drifting colored fog blobs that slowly orbit and
  // tint toward whichever player currently leads in ship count. Drawn before
  // the starfield so stars sit on top.
  {
    const stepIdx = Math.max(0, Math.min(frames.length - 1, Math.floor(cinemaState.stepFloat)));
    let leadCol = '#3a4880', laggCol = '#5a3a78';
    if (N_AGENTS >= 2) {
      const s0 = series.ships[0][stepIdx];
      const s1 = series.ships[1][stepIdx];
      const total = s0 + s1 || 1;
      const dom = (s0 - s1) / total;  // -1..+1
      const p0c = COLORS[0], p1c = COLORS[1];
      leadCol = dom >= 0 ? p0c : p1c;
      laggCol = dom >= 0 ? p1c : p0c;
    }
    const driftT = cinemaState.stepFloat * 0.04;
    bctx.globalCompositeOperation = 'lighter';
    for (let n = 0; n < 5; n++) {
      const seed = n * 17.3;
      const cx = (Math.sin(seed + driftT * 0.3) * 0.5 + 0.5) * W;
      const cy = (Math.cos(seed * 1.4 + driftT * 0.22) * 0.5 + 0.5) * H;
      const r = (0.18 + 0.08 * Math.sin(seed * 2 + driftT * 0.5)) * Math.max(W, H);
      const col = (n % 2 === 0) ? leadCol : laggCol;
      const g = bctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      g.addColorStop(0, col + '22');
      g.addColorStop(0.5, col + '10');
      g.addColorStop(1, col + '00');
      bctx.fillStyle = g;
      bctx.fillRect(0, 0, W, H);
    }
    bctx.globalCompositeOperation = 'source-over';
  }
  // Starfield drawn in canvas space (not warped by camera) so stars fill the
  // entire viewport including the bars outside the square game area.
  bctx.fillStyle = '#ffffff';
  for (let i = 0; i < 240; i++) {
    const seed = i * 13.37;
    const sxr = ((Math.sin(seed) * 0.5 + 0.5) * W);
    const syr = ((Math.cos(seed * 1.7) * 0.5 + 0.5) * H);
    bctx.globalAlpha = 0.25 + 0.4 * (0.5 + 0.5 * Math.sin(seed * 3 + cinemaState.stepFloat * 0.02));
    bctx.fillRect(sxr, syr, 1.2 + (i % 5 === 0 ? 0.8 : 0), 1.2);
  }
  bctx.globalAlpha = 1;

  // Sun -- outer halo tints toward whichever player currently leads in ship
  // count. Core stays warm so the sun is still recognisable; only the rim
  // and corona swap colour.
  const [sunX, sunY] = projectCam(50, 50, cam, W, H);
  const sunR = 10 * cam.zoom * gameScale;
  let sunHaloCol = '#ff5a1a';
  let sunCoronaCol = '#ffb45a';
  if (N_AGENTS >= 2) {
    const stepIdxS = Math.max(0, Math.min(frames.length - 1, Math.floor(cinemaState.stepFloat)));
    let bestP = -1, bestShips = -1;
    for (let p = 0; p < N_AGENTS; p++) {
      const s = series.ships[p] ? series.ships[p][stepIdxS] : 0;
      if (s > bestShips) { bestShips = s; bestP = p; }
    }
    if (bestP >= 0 && bestShips > 0) {
      sunHaloCol = COLORS[bestP];
      sunCoronaCol = COLORS[bestP];
    }
  }
  const sg = bctx.createRadialGradient(sunX, sunY, sunR * 0.2, sunX, sunY, sunR);
  sg.addColorStop(0, '#fff5d2');
  sg.addColorStop(0.4, '#ffb84a');
  sg.addColorStop(1, sunHaloCol + 'cc');
  bctx.fillStyle = sg;
  bctx.beginPath(); bctx.arc(sunX + sx, sunY + sy, sunR, 0, PI2); bctx.fill();
  // Sun corona pulse (matches halo tint).
  const pulse = (Math.sin(cinemaState.stepFloat * 0.3) + 1) * 0.5;
  bctx.strokeStyle = sunCoronaCol + Math.floor((0.25 - pulse * 0.15) * 255).toString(16).padStart(2, '0');
  bctx.lineWidth = 3;
  bctx.beginPath(); bctx.arc(sunX + sx, sunY + sy, sunR * (1.2 + pulse * 0.1), 0, PI2); bctx.stroke();

  // Planet trails (cinema-style faint trail)
  if (trailsChk.checked) {
    const trail = Math.min(20, stepFloor);
    for (let s = stepFloor - trail; s < stepFloor; s++) {
      const a = (s - (stepFloor - trail)) / trail;
      for (const pl of frames[s].planets) {
        const [px, py] = projectCam(pl[2], pl[3], cam, W, H);
        bctx.globalAlpha = a * 0.12;
        bctx.fillStyle = pl[1] >= 0 ? COLORS[pl[1]] : '#666';
        bctx.beginPath(); bctx.arc(px + sx, py + sy, Math.max(1, pl[4] * cam.zoom * gameScale * 0.3), 0, PI2); bctx.fill();
      }
    }
    bctx.globalAlpha = 1;
  }

  // Planets (with texture). Position interpolates between fa and fb, but
  // *displayed* attributes (owner, ships, production) come from the previous
  // frame so the planet only "captures" / changes color at the step boundary
  // -- not partway through the slow-mo approach to the event.
  for (const p of fb.planets) {
    const prev = planetsA[p[0]];
    const interp = getInterpolatedPlanet(p, prev, alpha, av);
    const showP = prev || p;
    const [px, py] = projectCam(interp.x, interp.y, cam, W, H);
    const sr = showP[4] * cam.zoom * gameScale;
    const cometSrc = prev ? (fa.comet_planet_ids || []) : (fb.comet_planet_ids || []);
    const isComet = cometSrc.includes(showP[0]);
    drawPlanetTexture(bctx, px + sx, py + sy, sr, showP[1], isComet, showP[6]);
    // ship count
    bctx.fillStyle = '#fff';
    const fs = Math.max(9, sr * 0.65);
    bctx.font = `bold ${fs}px sans-serif`;
    bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
    bctx.fillText(String(showP[5]), px + sx, py + sy);
  }

  // Fleet trails -- draw the polylines (mutation is in tickWorldState).
  {
    // Draw trails (under fleets) -- one polyline per fleet, mid alpha.
    bctx.globalAlpha = 0.45;
    bctx.lineCap = 'round';
    for (const [fid, tr] of cinemaState.fleetTrails) {
      if (tr.length < 2) continue;
      const c = tr[tr.length - 1].owner >= 0 ? COLORS[tr[tr.length - 1].owner] : '#fff';
      bctx.strokeStyle = c;
      bctx.lineWidth = Math.max(1.5, cam.zoom * gameScale * 0.18);
      bctx.beginPath();
      const [px0, py0] = projectCam(tr[0].x, tr[0].y, cam, W, H);
      bctx.moveTo(px0 + sx, py0 + sy);
      for (let i = 1; i < tr.length; i++) {
        const [px, py] = projectCam(tr[i].x, tr[i].y, cam, W, H);
        bctx.lineTo(px + sx, py + sy);
      }
      bctx.stroke();
    }
    bctx.globalAlpha = 1;
    bctx.lineCap = 'butt';
  }

  // Comet trajectory preview -- dashed faint line forward from each comet.
  if (cinemaState.ultra) {
    for (const g of (fb.comets || [])) {
      const idx = g.path_index;
      for (let i = 0; i < g.planet_ids.length; i++) {
        const path = g.paths[i];
        if (!path || idx >= path.length - 1) continue;
        bctx.setLineDash([4, 4]);
        bctx.strokeStyle = '#9ad7ff55';
        bctx.lineWidth = 1.5;
        bctx.beginPath();
        for (let j = idx; j < path.length; j++) {
          const [px, py] = projectCam(path[j][0], path[j][1], cam, W, H);
          if (j === idx) bctx.moveTo(px + sx, py + sy);
          else bctx.lineTo(px + sx, py + sy);
        }
        bctx.stroke();
        bctx.setLineDash([]);
      }
    }
  }

  // Fleets -- three cases:
  //   (a) fleet exists in both fa and fb        -> linear interpolate
  //   (b) fleet new in fb (just launched)        -> slide out from source planet
  //   (c) fleet in fa, gone in fb (destroyed)    -> project forward, fade at end
  const drawFleetTri = (x, y, angle, ships, owner, fadeA = 1) => {
    const [px, py] = projectCam(x, y, cam, W, H);
    const c = owner >= 0 ? COLORS[owner] : '#fff';
    const fm = fleetSizeMultiplier(ships);
    bctx.globalAlpha = fadeA;
    bctx.shadowColor = c; bctx.shadowBlur = 6 * fm;
    bctx.save();
    bctx.translate(px + sx, py + sy);
    bctx.rotate(angle);
    const sc = cam.zoom * gameScale * fm;
    bctx.fillStyle = c;
    bctx.beginPath();
    bctx.moveTo(1.3 * sc, 0);
    bctx.lineTo(-0.9 * sc, 0.7 * sc);
    bctx.lineTo(-0.9 * sc, -0.7 * sc);
    bctx.closePath();
    bctx.fill();
    bctx.fillStyle = c + '66';
    bctx.beginPath();
    bctx.moveTo(-0.9 * sc, 0.4 * sc);
    bctx.lineTo(-3 * sc, 0);
    bctx.lineTo(-0.9 * sc, -0.4 * sc);
    bctx.closePath();
    bctx.fill();
    bctx.restore();
    bctx.shadowBlur = 0;
    bctx.globalAlpha = 1;
  };

  // (a) + (b)
  for (const f of fb.fleets) {
    const prev = fleetsA[f[0]];
    if (prev) {
      // (a): interpolate between the two known positions.
      const interp = getInterpolatedFleet(f, prev, alpha);
      drawFleetTri(interp.x, interp.y, interp.angle, interp.ships, interp.owner);
    } else {
      // (b): newly launched. Slide out from the source planet so it looks
      // like ships physically left the planet.
      const srcPid = f[5];
      const srcA = fa.planets.find(p => p[0] === srcPid);
      const srcB = fb.planets.find(p => p[0] === srcPid);
      const src = srcA || srcB;
      if (src) {
        const lx = src[2] + (f[2] - src[2]) * alpha;
        const ly = src[3] + (f[3] - src[3]) * alpha;
        // Pop-in: small fade in at the very start.
        const popA = alpha < 0.15 ? alpha / 0.15 : 1;
        drawFleetTri(lx, ly, f[4], f[6], f[1], popA);
      } else {
        drawFleetTri(f[2], f[3], f[4], f[6], f[1]);
      }
    }
  }

  // (c): fleet destroyed between fa and fb. Project forward to make the
  // impact look continuous instead of snapping out of existence.
  for (const f of fa.fleets) {
    if (fbIds.has(f[0])) continue;
    const speed = fleetSpeed(f[6]);
    const x = f[2] + Math.cos(f[4]) * speed * alpha;
    const y = f[3] + Math.sin(f[4]) * speed * alpha;
    const fade = alpha > 0.92 ? Math.max(0, (1 - (alpha - 0.92) / 0.08)) : 1;
    drawFleetTri(x, y, f[4], f[6], f[1], fade);
  }

  // Running STREAK ×N tag (single instance, hops to whichever planet was
  // just captured). Same fade-in / fade-out shape as record-fleet tags.
  // Aging happens in tickWorldState; this block only draws.
  if (cinemaState.streakTag) {
    const tag = cinemaState.streakTag;
    const planet = fb.planets.find(p => p[0] === tag.pid);
    if (planet) {
      const prevP = planetsA[tag.pid];
      const interp = getInterpolatedPlanet(planet, prevP, alpha, av);
      const [px, py] = projectCam(interp.x, interp.y, cam, W, H);
      const sr = planet[4] * cam.zoom * gameScale;
      const fade = tag.age < 12 ? tag.age / 12 :
                   (tag.age > tag.life - 30 ? (tag.life - tag.age) / 30 : 1);
      const rise = Math.min(1, tag.age / 60) * (gameScale * 1.2);
      const fs = Math.max(10, gameScale * cam.zoom * 1.4);
      bctx.globalAlpha = fade;
      bctx.font = `bold ${fs}px sans-serif`;
      bctx.textAlign = 'center'; bctx.textBaseline = 'bottom';
      bctx.shadowColor = '#000'; bctx.shadowBlur = 4;
      bctx.fillStyle = tag.color;
      bctx.fillText(tag.text, px + sx, py + sy - sr - 6 - rise);
      bctx.shadowBlur = 0;
      bctx.globalAlpha = 1;
    }
  }

  // Record-fleet tags floating on the actual fleet. Aging is in tickWorldState.
  if (cinemaState.recordFleetTags && cinemaState.recordFleetTags.length) {
    for (let i = 0; i < cinemaState.recordFleetTags.length; i++) {
      const tag = cinemaState.recordFleetTags[i];
      const cur = fb.fleets.find(f => f[0] === tag.fleetId);
      const prev = cur ? fleetsA[tag.fleetId] : null;
      if (!cur) continue;
      const x = prev ? prev[2] + (cur[2] - prev[2]) * alpha : cur[2];
      const y = prev ? prev[3] + (cur[3] - prev[3]) * alpha : cur[3];
      const [px, py] = projectCam(x, y, cam, W, H);
      const fade = tag.age < 15 ? tag.age / 15 :
                   (tag.age > tag.life - 30 ? (tag.life - tag.age) / 30 : 1);
      const fs = Math.max(10, gameScale * cam.zoom * 1.6);
      bctx.globalAlpha = fade;
      bctx.font = `bold ${fs}px sans-serif`;
      bctx.textAlign = 'center'; bctx.textBaseline = 'bottom';
      bctx.shadowColor = '#000'; bctx.shadowBlur = 4;
      bctx.fillStyle = tag.color;
      bctx.fillText(tag.text, px + sx, py + sy - 8);
      bctx.shadowBlur = 0;
      bctx.globalAlpha = 1;
    }
  }

  // Contested planet badges + damage numbers
  if (cinemaState.ultra) {
    // Badges -- only while the contested window is active for this planet.
    const cw = cinemaState.ultra.contestedWindows;
    const curT = cinemaState.stepFloat;
    for (const p of fb.planets) {
      const win = cw[p[0]];
      if (!win || curT < win.startT || curT > win.endT) continue;
      // Count flips that have actually happened by the current playback time.
      let liveCount = 0;
      for (const t of win.flipTicks) if (t <= curT) liveCount++;
      if (liveCount < 3) continue;  // not yet labeled until 3 flips have aired
      const lastFlipT = win.flipTicks[liveCount - 1];
      const sinceFlip = curT - lastFlipT;
      // Inactivity fade: full alpha for the first 20 ticks after a flip, then
      // ramp down to 0 by ~40 ticks of quiet. Skip drawing entirely once gone.
      const fadeAlpha = sinceFlip <= 20 ? 1
                       : sinceFlip >= 40 ? 0
                       : 1 - (sinceFlip - 20) / 20;
      if (fadeAlpha <= 0.02) continue;
      const prev = planetsA[p[0]];
      const interp = getInterpolatedPlanet(p, prev, alpha, av);
      const [px, py] = projectCam(interp.x, interp.y, cam, W, H);
      const sr = p[4] * cam.zoom * gameScale;
      // Pulsing red ring -- briefly pulse harder right after a new flip.
      const flipBoost = sinceFlip < 8 ? (1 - sinceFlip / 8) : 0;
      const pulse = 0.7 + 0.3 * Math.sin(cinemaState.stepFloat * 0.4) + flipBoost * 0.6;
      bctx.globalAlpha = fadeAlpha;
      bctx.strokeStyle = `rgba(255, 90, 90, ${Math.min(1, 0.5 * pulse)})`;
      bctx.lineWidth = 2 + flipBoost * 2;
      bctx.beginPath();
      bctx.arc(px + sx, py + sy, sr + 4, 0, PI2);
      bctx.stroke();
      // Text badge. Drop it below the planet if there's no headroom above
      // (so it doesn't clash with the top progress bar/timeline strip).
      const fs = Math.max(9, gameScale * cam.zoom * 1.5);
      const textTop = py + sy - sr - 6 - fs;
      const placeBelow = textTop < 36;
      bctx.fillStyle = '#ff5a5a';
      bctx.font = `bold ${fs}px sans-serif`;
      bctx.textAlign = 'center';
      bctx.textBaseline = placeBelow ? 'top' : 'bottom';
      const ty = placeBelow ? py + sy + sr + 6 : py + sy - sr - 6;
      // Translucent backing for legibility
      const w = bctx.measureText(`CONTESTED ×${liveCount}`).width + 12;
      bctx.fillStyle = '#000a';
      bctx.fillRect(px + sx - w / 2, ty - (placeBelow ? 2 : fs + 2), w, fs + 4);
      bctx.fillStyle = '#ff5a5a';
      bctx.fillText(`CONTESTED ×${liveCount}`, px + sx, ty);
      bctx.globalAlpha = 1;
    }
    // Floating damage numbers (single draw with shadow for outline)
    bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
    for (let i = 0; i < cinemaState.damageNumbers.length; i++) {
      const d = cinemaState.damageNumbers[i];
      const a = d.age < 6 ? d.age / 6 : (d.age > d.life - 16 ? (d.life - d.age) / 16 : 1);
      const [px, py] = projectCam(d.x, d.y, cam, W, H);
      if (px < -30 || px > W + 30 || py < -30 || py > H + 30) continue;
      bctx.globalAlpha = a;
      const fs = Math.max(11, gameScale * cam.zoom * 1.9);
      bctx.font = `bold ${fs}px sans-serif`;
      bctx.shadowColor = '#000'; bctx.shadowBlur = 4;
      bctx.fillStyle = d.color;
      bctx.fillText(d.text, px + sx, py + sy);
      bctx.shadowBlur = 0;
      bctx.globalAlpha = 1;
    }
  }

  // Particles & flashes (aging happens in tickWorldState).
  bctx.shadowBlur = 0;
  for (let i = 0; i < cinemaState.particles.length; i++) {
    const p = cinemaState.particles[i];
    const a = 1 - p.age / p.life;
    const [px, py] = projectCam(p.x, p.y, cam, W, H);
    bctx.fillStyle = p.color;
    bctx.globalAlpha = a;
    bctx.beginPath(); bctx.arc(px + sx, py + sy, p.size * cam.zoom * gameScale * 0.4, 0, PI2); bctx.fill();
  }
  for (let i = 0; i < cinemaState.flashes.length; i++) {
    const fl = cinemaState.flashes[i];
    const a = 1 - fl.age / fl.life;
    const r = fl.maxRadius * easeOutCubic(Math.min(1, fl.age / fl.life));
    const [px, py] = projectCam(fl.x, fl.y, cam, W, H);
    bctx.globalAlpha = a;
    bctx.strokeStyle = fl.color; bctx.lineWidth = 2 * cam.zoom;
    bctx.beginPath(); bctx.arc(px + sx, py + sy, r * cam.zoom * gameScale, 0, PI2); bctx.stroke();
  }
  bctx.globalAlpha = 1;

  // Vignette
  const vg = bctx.createRadialGradient(W/2, H/2, Math.min(W,H) * 0.4, W/2, H/2, Math.max(W,H) * 0.75);
  vg.addColorStop(0, 'rgba(0,0,0,0)');
  vg.addColorStop(1, 'rgba(0,0,0,0.7)');
  bctx.fillStyle = vg;
  bctx.fillRect(0, 0, W, H);

  // Localized barrier flashes -- every fleet that hits an edge of the map
  // (not just game-enders) leaves a small radial red glow at its impact point.
  // The glow is centred on the boundary so half of it sits "outside" the map,
  // giving a force-field-membrane feel.
  if (cinemaState.barrierFlashes && cinemaState.barrierFlashes.length) {
    for (const bf of cinemaState.barrierFlashes) {
      const ap = 1 - bf.age / bf.life;
      const easedIn = bf.age < 6 ? bf.age / 6 : 1;
      const a = ap * easedIn;
      if (a <= 0.02) continue;
      const [px, py] = projectCam(bf.x, bf.y, cam, W, H);
      // Radius scales with ship size + zoom + life. Larger fleets ripple more.
      const baseR = (3 + Math.min(20, bf.ships) * 0.35) * cam.zoom * gameScale;
      const r = baseR * (0.7 + (1 - ap) * 0.6);  // expands slightly as it fades
      // Outer dim halo
      const g = bctx.createRadialGradient(px + sx, py + sy, 0,
                                          px + sx, py + sy, r * 1.8);
      g.addColorStop(0,   `rgba(255, 90, 90, ${a * 0.85})`);
      g.addColorStop(0.4, `rgba(255, 60, 60, ${a * 0.45})`);
      g.addColorStop(1,   'rgba(255, 30, 30, 0)');
      bctx.fillStyle = g;
      bctx.beginPath();
      bctx.arc(px + sx, py + sy, r * 1.8, 0, PI2);
      bctx.fill();
      // Hot inner core
      const g2 = bctx.createRadialGradient(px + sx, py + sy, 0,
                                           px + sx, py + sy, r * 0.55);
      g2.addColorStop(0, `rgba(255, 230, 200, ${a})`);
      g2.addColorStop(1, `rgba(255, 90, 90, 0)`);
      bctx.fillStyle = g2;
      bctx.beginPath();
      bctx.arc(px + sx, py + sy, r * 0.55, 0, PI2);
      bctx.fill();
      // Arc along the boundary edge at the hit point so the force-field shape
      // reads. Draw a short stroked line on the appropriate edge.
      const [bx1, by1] = projectCam(0, 0, cam, W, H);
      const [bx2, by2] = projectCam(100, 100, cam, W, H);
      bctx.strokeStyle = `rgba(255, 170, 170, ${a * 0.9})`;
      bctx.lineWidth = 2;
      bctx.shadowColor = `rgba(255, 80, 80, ${a})`;
      bctx.shadowBlur = 10;
      bctx.beginPath();
      const segLen = r * 1.6;
      if (bf.edge === 'T') {
        bctx.moveTo(px + sx - segLen, by1 + sy);
        bctx.lineTo(px + sx + segLen, by1 + sy);
      } else if (bf.edge === 'B') {
        bctx.moveTo(px + sx - segLen, by2 + sy);
        bctx.lineTo(px + sx + segLen, by2 + sy);
      } else if (bf.edge === 'L') {
        bctx.moveTo(bx1 + sx, py + sy - segLen);
        bctx.lineTo(bx1 + sx, py + sy + segLen);
      } else {
        bctx.moveTo(bx2 + sx, py + sy - segLen);
        bctx.lineTo(bx2 + sx, py + sy + segLen);
      }
      bctx.stroke();
      bctx.shadowBlur = 0;
    }
  }

  // Edge-of-map red border (game-ending OOB elimination effect). Lerping
  // happens in tickWorldState; per-pane just renders it.
  if (cinemaState.edgeFlashAlpha > 0.02) {
    const ea = cinemaState.edgeFlashAlpha;
    const [ex1, ey1] = projectCam(0, 0, cam, W, H);
    const [ex2, ey2] = projectCam(100, 100, cam, W, H);
    const pulse = 0.85 + 0.15 * Math.sin(cinemaState.stepFloat * 1.5);
    const lw = (3 + 6 * ea) * pulse;
    bctx.shadowColor = `rgba(255, 70, 70, ${ea * 0.95})`;
    bctx.shadowBlur = 18 * ea;
    bctx.strokeStyle = `rgba(255, 60, 60, ${ea * 0.9})`;
    bctx.lineWidth = lw;
    bctx.strokeRect(ex1 + sx, ey1 + sy, ex2 - ex1, ey2 - ey1);
    bctx.shadowBlur = 0;
    bctx.strokeStyle = `rgba(255, 170, 170, ${ea})`;
    bctx.lineWidth = Math.max(1, lw * 0.35);
    bctx.strokeRect(ex1 + sx, ey1 + sy, ex2 - ex1, ey2 - ey1);
  }

    // Close the per-pane scope (drops back to full-canvas W/H below).
    if (multi) bctx.restore();
  } // end per-pane loop

  // ----- Pane borders ----- (multi shot only)
  if (panes.length > 1 && shotCur && shotCur.kind === 'multi') {
    drawPaneBorders(panes, W, H, shotCur);
  }

  // ----- Full-canvas overlays -----
  // Victory flash (aging in tickWorldState).
  if (cinemaState.victoryFlash) {
    const vf = cinemaState.victoryFlash;
    {
      const a = vf.age < 8 ? vf.age / 8 : (1 - (vf.age - 8) / (vf.life - 8));
      bctx.globalAlpha = Math.max(0, a) * 0.45;
      bctx.fillStyle = vf.color;
      bctx.fillRect(0, 0, W, H);
      bctx.globalAlpha = 1;
    }
  }

  // Per-pane captions (only used in multi-shot with mixed event types).
  if (cinemaState.paneCaptions && cinemaState.paneCaptions.length &&
      shotCur && shotCur.kind === 'multi') {
    drawPaneCaptions(cinemaState.paneCaptions, shotCur, W, H);
  }

  // Captions (aging in tickWorldState). Single overall caption above hype panel.
  if (cinemaState.captions.length) {
    const c = cinemaState.captions[0];
    {
      let a = (c.age < 10) ? c.age / 10 : (c.age > c.life - 20 ? (c.life - c.age) / 20 : 1);
      // Fade caption while the camera is decompressing (zooming back out) so
      // it doesn't linger on top of the wide view. Non-game-enders fade fully;
      // game-ending FINAL BLOW / LAST STAND captions keep a residual presence
      // (>=0.35) since the match is over and they're the headline.
      if (shotCur && shotCur.kind === 'decompress') {
        const t = Math.min(1, cinemaState.shotFrame / Math.max(1, shotCur.duration));
        const floor = (c.priority || 1) >= 5 ? 0.35 : 0;
        a *= floor + (1 - floor) * (1 - t);
      }
      bctx.globalAlpha = a;
      // Cap title font so 4K monitors don't get a 200px headline.
      const titleFs = Math.min(72, Math.floor(W * 0.045));
      const subFs = Math.min(26, Math.floor(W * 0.018));
      bctx.font = `bold ${titleFs}px sans-serif`;
      bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
      // Sit the caption box just above the hype panel (which starts at H*0.78)
      // rather than dead-center -- it was reading as obstructive at 0.60.
      const boxH = titleFs * (c.sub ? 2.0 : 1.5);
      const boxBottom = H * 0.76;
      const boxTop = boxBottom - boxH;
      const titleY = boxTop + titleFs * 0.75;
      const subY = titleY + titleFs * 0.95;
      bctx.fillStyle = '#000a';
      bctx.fillRect(0, boxTop, W, boxH);
      bctx.fillStyle = c.color;
      bctx.fillText(c.text, W/2, titleY);
      if (c.sub) {
        bctx.font = `${subFs}px sans-serif`;
        bctx.fillStyle = '#fff';
        bctx.fillText(c.sub, W/2, subY);
      }
      bctx.globalAlpha = 1;
    }
  }

  // (Scoreboard lower-third removed -- redundant with hype overlay)

  // Title card / final card overlays
  const shot = cinemaState.shots[cinemaState.shotIdx];
  if (shot && shot.kind === 'title') {
    const p = Math.min(1, cinemaState.shotFrame / 20);
    bctx.globalAlpha = p;
    bctx.fillStyle = '#000c'; bctx.fillRect(0, 0, W, H);
    bctx.font = `bold ${Math.floor(W * 0.09)}px sans-serif`;
    bctx.fillStyle = '#fff'; bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
    bctx.fillText('ORBIT WARS', W/2, H/2 - W * 0.04);
    bctx.font = `${Math.floor(W * 0.04)}px sans-serif`;
    bctx.fillStyle = '#ffd966';
    bctx.fillText(NAMES.slice(0, N_AGENTS).join('  vs  '), W/2, H/2 + W * 0.02);
    bctx.font = `${Math.floor(W * 0.022)}px sans-serif`;
    bctx.fillStyle = '#8a96b8';
    bctx.fillText(`${cinemaState.eventCount} hyped events  ·  ${frames.length} ticks`, W/2, H/2 + W * 0.06);
    bctx.globalAlpha = 1;
  } else if (shot && shot.kind === 'final') {
    const rewards = frames[frames.length - 1].rewards;
    let winnerIdx = -1, best = -Infinity;
    for (let p = 0; p < N_AGENTS; p++) {
      if (rewards[p] !== null && rewards[p] !== undefined && rewards[p] > best) { best = rewards[p]; winnerIdx = p; }
    }
    const p = Math.min(1, cinemaState.shotFrame / 25);
    bctx.globalAlpha = 0.85 * p;
    bctx.fillStyle = '#000'; bctx.fillRect(0, 0, W, H);
    bctx.globalAlpha = p;
    bctx.font = `bold ${Math.floor(W * 0.05)}px sans-serif`;
    bctx.fillStyle = '#8a96b8'; bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
    bctx.fillText('WINNER', W/2, H/2 - W * 0.07);
    bctx.font = `bold ${Math.floor(W * 0.1)}px sans-serif`;
    bctx.fillStyle = winnerIdx >= 0 ? COLORS[winnerIdx] : '#fff';
    bctx.fillText(winnerIdx >= 0 ? NAMES[winnerIdx] : 'TIE', W/2, H/2);
    bctx.font = `${Math.floor(W * 0.025)}px sans-serif`;
    bctx.fillStyle = '#fff';
    for (let i = 0; i < N_AGENTS; i++) {
      bctx.fillStyle = COLORS[i];
      bctx.fillText(`${NAMES[i]}: ${series.ships[i][frames.length - 1]} ships, ${series.planets[i][frames.length - 1]} planets`, W/2, H/2 + W * 0.06 + i * W * 0.03);
    }
    bctx.globalAlpha = 1;
  }

  // Mid-action stat callout during slowmo/approach shots.
  // Drawn in the top-LEFT, well clear of the top-right Exit button and the
  // bottom hype panel.
  {
    const curShot = cinemaState.shots[cinemaState.shotIdx];
    if (curShot && (curShot.kind === 'slowmo' || curShot.kind === 'approach') && curShot.event) {
      const e = curShot.event;
      let lines = [];
      if (e.type === 'big_fleet' || e.type === 'impact' || e.type === 'comet_sweep') {
        lines.push(`FLEET: ${e.ships ?? '?'} SHIPS`);
        if (e.targetPid !== null && e.targetPid !== undefined) {
          const ttI = Math.max(0, e.t - cinemaState.stepFloat);
          if (ttI > 0.1) lines.push(`IMPACT IN: ${ttI.toFixed(1)}t`);
        }
      } else if (e.type === 'flip') {
        lines.push(`PLANET ${e.pid}  ·  +${e.prod}/TURN`);
      } else if (e.type === 'lead_change') {
        lines.push(`NEW LEADER: ${NAMES[e.toPlayer]}`);
      } else if (e.type === 'comet_spawn') {
        lines.push('COMET TRAJECTORY LOCKED');
      }
      if (lines.length) {
        const fs = Math.min(22, Math.max(12, W * 0.014));
        bctx.font = `bold ${fs}px sans-serif`;
        bctx.textAlign = 'left'; bctx.textBaseline = 'middle';
        bctx.globalAlpha = 0.92;
        let y = H * 0.10;
        const padBox = fs * 0.5;
        // Compute box width once based on the widest line
        let maxW = 0;
        for (const ln of lines) maxW = Math.max(maxW, bctx.measureText(ln).width);
        bctx.fillStyle = '#000a';
        bctx.fillRect(W * 0.025, y - fs * 0.8, maxW + padBox * 2, lines.length * fs * 1.4 + padBox);
        for (const ln of lines) {
          bctx.fillStyle = '#ffd966';
          bctx.fillText(ln, W * 0.025 + padBox, y);
          y += fs * 1.4;
        }
        bctx.globalAlpha = 1;
      }
    }
  }

  // Slim progress bar at very top edge
  const pct = cinemaState.stepFloat / (frames.length - 1);
  bctx.fillStyle = '#ffffff15'; bctx.fillRect(0, 0, W, 3);
  bctx.fillStyle = '#ffd966'; bctx.fillRect(0, 0, W * pct, 3);

  // Match progress timeline just below the slim bar.
  {
    const tlY = 8, tlH = 14;
    bctx.fillStyle = 'rgba(0,0,0,0.45)';
    bctx.fillRect(0, tlY, W, tlH);
    // Event dots (use the selected events list from shots)
    for (const shot of cinemaState.shots) {
      if (!shot.event) continue;
      const t = shot.event.t;
      const x = (t / (frames.length - 1)) * W;
      let dotColor = '#ffd966';
      switch (shot.event.type) {
        case 'flip': dotColor = shot.event.toOwner >= 0 ? COLORS[shot.event.toOwner] : '#fff'; break;
        case 'comet_sweep': case 'comet_spawn': dotColor = '#9ad7ff'; break;
        case 'lead_change': dotColor = '#ffd966'; break;
        case 'big_fleet': dotColor = shot.event.owner >= 0 ? COLORS[shot.event.owner] : '#fff'; break;
      }
      bctx.fillStyle = dotColor;
      bctx.beginPath(); bctx.arc(x, tlY + tlH / 2, 3, 0, PI2); bctx.fill();
    }
    // Playhead
    const phX = pct * W;
    bctx.fillStyle = '#ffffff';
    bctx.beginPath();
    bctx.moveTo(phX, tlY); bctx.lineTo(phX - 4, tlY - 5); bctx.lineTo(phX + 4, tlY - 5);
    bctx.closePath(); bctx.fill();
  }

  // Bottom hype panel
  drawHypePanel();
}

function drawHypePanel() {
  if (!cinemaState || hypeCanvas.offsetParent === null) return;
  const W = hypeCanvas.width, H = hypeCanvas.height;
  hctx.clearRect(0, 0, W, H);  // panel parent has its own gradient bg
  const step = Math.max(0, Math.min(frames.length - 1, Math.floor(cinemaState.stepFloat)));

  // Smooth tracked values
  const p0Ships = N_AGENTS >= 1 ? series.ships[0][step] : 0;
  const p1Ships = N_AGENTS >= 2 ? series.ships[1][step] : 0;
  const total = Math.max(1, p0Ships + p1Ships);
  const targetShare = p0Ships / total;
  cinemaState.smoothedShare += (targetShare - cinemaState.smoothedShare) * 0.08;
  const flow = step > 0 ? series.shipDelta[step] : 0;
  cinemaState.smoothedFlow += (flow - cinemaState.smoothedFlow) * 0.15;

  // Three sections in a row
  const colW = W / 3;
  const padX = 16, padY = 12;

  // ===== Section 1: Dominance tug-of-war =====
  {
    const left = padX, right = colW - padX;
    const innerW = right - left;
    // Vertical bands (% of H):  label 0..18, names 22..42, totals 46..78, bar 84..100
    const labelY = H * 0.06;
    const nameY  = H * 0.30;
    const totalY = H * 0.60;
    const barY   = H * 0.84;
    const barH   = Math.max(8, H * 0.10);
    const fLabel = Math.max(10, H * 0.10);
    const fName  = Math.max(13, H * 0.16);
    const fTotal = Math.max(22, H * 0.32);

    hctx.fillStyle = '#8a96b8';
    hctx.font = `${fLabel}px sans-serif`;
    hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
    hctx.fillText('DOMINANCE', left, labelY);

    hctx.font = `bold ${fName}px sans-serif`;
    hctx.textBaseline = 'middle';
    hctx.fillStyle = COLORS[0];
    hctx.textAlign = 'left';
    hctx.fillText(NAMES[0] || 'P0', left, nameY);
    if (N_AGENTS >= 2) {
      hctx.fillStyle = COLORS[1];
      hctx.textAlign = 'right';
      hctx.fillText(NAMES[1] || 'P1', right, nameY);
    }

    hctx.font = `bold ${fTotal}px sans-serif`;
    hctx.fillStyle = '#fff';
    hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
    hctx.fillText(String(p0Ships), left, totalY);
    if (N_AGENTS >= 2) {
      hctx.textAlign = 'right';
      hctx.fillText(String(p1Ships), right, totalY);
    }

    // Bar
    hctx.fillStyle = '#1f2a4a';
    hctx.fillRect(left, barY, innerW, barH);
    const splitX = left + innerW * cinemaState.smoothedShare;
    const g0 = hctx.createLinearGradient(left, 0, splitX, 0);
    g0.addColorStop(0, COLORS[0]); g0.addColorStop(1, COLORS[0] + 'aa');
    hctx.fillStyle = g0;
    hctx.fillRect(left, barY, splitX - left, barH);
    if (N_AGENTS >= 2) {
      const g1 = hctx.createLinearGradient(splitX, 0, right, 0);
      g1.addColorStop(0, COLORS[1] + 'aa'); g1.addColorStop(1, COLORS[1]);
      hctx.fillStyle = g1;
      hctx.fillRect(splitX, barY, right - splitX, barH);
    }
    const midX = left + innerW * 0.5;
    hctx.strokeStyle = '#ffffff66'; hctx.lineWidth = 1;
    hctx.beginPath(); hctx.moveTo(midX, barY - 2); hctx.lineTo(midX, barY + barH + 2); hctx.stroke();
  }

  // ===== Section 2: Recent events feed =====
  {
    const left = colW + padX, right = colW * 2 - padX;
    const labelY = H * 0.06;
    const fLabel = Math.max(10, H * 0.10);
    hctx.fillStyle = '#8a96b8';
    hctx.font = `${fLabel}px sans-serif`;
    hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
    hctx.fillText('HIGHLIGHTS', left, labelY);

    const lineH = Math.max(20, H * 0.20);
    let y = H * 0.22;
    const maxLines = Math.floor((H - y) / lineH);
    for (let i = 0; i < Math.min(maxLines, cinemaState.eventLog.length); i++) {
      const ev = cinemaState.eventLog[i];
      const a = Math.max(0.35, 1 - i * 0.18);
      hctx.globalAlpha = a;
      // Pip dot
      hctx.fillStyle = ev.color;
      hctx.beginPath();
      hctx.arc(left + 6, y + lineH / 2, Math.max(3, H * 0.025), 0, PI2);
      hctx.fill();
      // Text (single line, larger so it reads at a glance)
      hctx.font = `bold ${Math.max(13, H * 0.13)}px sans-serif`;
      hctx.fillStyle = ev.color;
      hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
      hctx.fillText(ev.text, left + 20, y + lineH / 2);
      // Step on the right
      hctx.font = `${Math.max(10, H * 0.085)}px sans-serif`;
      hctx.fillStyle = '#8a96b8';
      hctx.textAlign = 'right';
      hctx.fillText(`t${ev.t}`, right, y + lineH / 2);
      y += lineH;
    }
    hctx.globalAlpha = 1;
    if (cinemaState.eventLog.length === 0) {
      hctx.fillStyle = '#444a66';
      hctx.font = `italic ${Math.max(12, H * 0.12)}px sans-serif`;
      hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
      hctx.fillText('Waiting for first hype event...', left, y + lineH / 2);
    }
  }

  // ===== Section 3: Big numbers =====
  {
    const left = colW * 2 + padX, right = W - padX;
    const labelY = H * 0.06;
    const fLabel = Math.max(10, H * 0.10);
    hctx.fillStyle = '#8a96b8';
    hctx.font = `${fLabel}px sans-serif`;
    hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
    hctx.fillText('STATS', left, labelY);

    // 2x2 grid below the label.
    const gridTop = H * 0.18;
    const gridBot = H * 0.98;
    const cellW = (right - left) / 2;
    const cellH = (gridBot - gridTop) / 2;
    const fleetsP0 = N_AGENTS >= 1 ? frames[step].fleets.filter(f => f[1] === 0).length : 0;
    const fleetsP1 = N_AGENTS >= 2 ? frames[step].fleets.filter(f => f[1] === 1).length : 0;
    const planetsP0 = N_AGENTS >= 1 ? series.planets[0][step] : 0;
    const planetsP1 = N_AGENTS >= 2 ? series.planets[1][step] : 0;
    const flowSign = cinemaState.smoothedFlow >= 0 ? 1 : -1;
    const flowVal = Math.abs(cinemaState.smoothedFlow);
    const flowColor = flowSign >= 0 ? (COLORS[0] || '#fff') : (COLORS[1] || '#fff');

    const cells = [
      { label: 'PLANETS', val: N_AGENTS >= 2 ? `${planetsP0} : ${planetsP1}` : `${planetsP0}`, color: '#fff' },
      { label: 'FLEETS',  val: N_AGENTS >= 2 ? `${fleetsP0} : ${fleetsP1}` : `${fleetsP0}`,   color: '#fff' },
      { label: 'EVENTS',  val: `${cinemaState.triggeredEvents.size} / ${cinemaState.eventCount}`, color: '#ffd966' },
      { label: 'FLOW/t',  val: `${flowSign >= 0 ? '+' : '-'}${flowVal.toFixed(1)}`, color: flowColor },
    ];
    const subLabel = Math.max(9, H * 0.09);
    const subValue = Math.max(18, H * 0.22);
    for (let i = 0; i < 4; i++) {
      const cx = left + (i % 2) * cellW;
      const cy = gridTop + Math.floor(i / 2) * cellH;
      hctx.fillStyle = '#8a96b8';
      hctx.font = `${subLabel}px sans-serif`;
      hctx.textAlign = 'left'; hctx.textBaseline = 'middle';
      hctx.fillText(cells[i].label, cx, cy + cellH * 0.22);
      hctx.fillStyle = cells[i].color;
      hctx.font = `bold ${subValue}px sans-serif`;
      hctx.textBaseline = 'middle';
      hctx.fillText(cells[i].val, cx, cy + cellH * 0.65);
    }
  }
}

function drawScoreboard(W, H, stepIdx) {
  const lh = Math.floor(H * 0.06);
  const y = H - lh - 6;
  bctx.fillStyle = '#000a';
  bctx.fillRect(0, y, W, lh + 6);
  bctx.textBaseline = 'middle';
  for (let p = 0; p < N_AGENTS; p++) {
    const x = W * (0.04 + p * 0.45);
    bctx.fillStyle = COLORS[p];
    bctx.fillRect(x, y + 6, 10, lh - 6);
    bctx.fillStyle = '#fff';
    bctx.textAlign = 'left';
    bctx.font = `bold ${Math.floor(H * 0.025)}px sans-serif`;
    bctx.fillText(NAMES[p] || ('P' + p), x + 18, y + lh * 0.4);
    bctx.font = `${Math.floor(H * 0.022)}px sans-serif`;
    bctx.fillStyle = '#bbb';
    bctx.fillText(`${series.ships[p][stepIdx]} ships  ·  ${series.planets[p][stepIdx]} planets`, x + 18, y + lh * 0.7);
  }
}

function advanceShot() {
  cinemaState.shotIdx++;
  cinemaState.shotFrame = 0;
  if (cinemaState.shotIdx >= cinemaState.shots.length) {
    stopCinema();
    return false;
  }
  const shot = cinemaState.shots[cinemaState.shotIdx];
  if (shot.target) {
    cinemaState.cam.tcx = shot.target.cx;
    cinemaState.cam.tcy = shot.target.cy;
    cinemaState.cam.tzoom = shot.target.zoom;
  }
  if (shot.fromStep !== undefined) {
    cinemaState.stepFloat = Math.max(cinemaState.stepFloat, shot.fromStep);
  }
  if (shot.atStep !== undefined) {
    cinemaState.stepFloat = Math.max(cinemaState.stepFloat, shot.atStep);
  }
  // Edge-out border: start fading in on approach/slowmo of edge_out shots,
  // start fading out partway through the impact hold.
  const isEdgeShot = shot.event && shot.event.type === 'edge_out';
  if (isEdgeShot && (shot.kind === 'approach' || shot.kind === 'slowmo')) {
    cinemaState.edgeFlashTarget = 1;
  }
  return true;
}

function cinemaTick() {
  if (!cinemaMode || !cinemaState) return;
  const shot = cinemaState.shots[cinemaState.shotIdx];
  if (!shot) { stopCinema(); return; }
  cinemaState.shotFrame++;
  switch (shot.kind) {
    case 'title': {
      // Hold on title card without advancing time much
      if (cinemaState.shotFrame >= shot.duration) advanceShot();
      break;
    }
    case 'fill': {
      // Fast-forward, but capped so things don't blur.
      const remaining = shot.toStep - cinemaState.stepFloat;
      const stepsThisFrame = Math.max(1.0, Math.min(2.2, remaining / 40));
      cinemaState.stepFloat += stepsThisFrame;
      if (cinemaState.stepFloat >= shot.toStep) {
        cinemaState.stepFloat = shot.toStep;
        advanceShot();
      }
      break;
    }
    case 'ambient': {
      // Real-time-ish playback at wide view, optionally ramping speed up.
      const stepsIn = cinemaState.stepFloat - shot.fromStep;
      const ramp = shot.rampSteps || 0;
      const t = ramp > 0 ? Math.min(1, stepsIn / ramp) : 1;
      const rate = (shot.stepsPerFrameStart ?? 0.18) +
                   ((shot.stepsPerFrameEnd ?? shot.stepsPerFrameStart ?? 0.18) -
                    (shot.stepsPerFrameStart ?? 0.18)) * t;
      cinemaState.stepFloat += rate;
      if (cinemaState.stepFloat >= shot.toStep) {
        cinemaState.stepFloat = shot.toStep;
        advanceShot();
      }
      break;
    }
    case 'decompress': {
      // Pause real time; just let the camera ease back to wide.
      if (cinemaState.shotFrame >= shot.duration) advanceShot();
      break;
    }
    case 'approach': {
      // Slow approach, time advances ~0.25 step/frame so the camera glide reads.
      cinemaState.stepFloat = Math.min(shot.event.t - 1, cinemaState.stepFloat + 0.25);
      if (cinemaState.shotFrame >= shot.duration) advanceShot();
      break;
    }
    case 'slowmo': {
      cinemaState.stepFloat += shot.stepsPerFrame;
      if (cinemaState.stepFloat >= shot.toStep) {
        cinemaState.stepFloat = shot.toStep;
        triggerEventEffects(cinemaState, shot.event);
        advanceShot();
      }
      break;
    }
    case 'impact': {
      // Freeze frame on the event step; effects already triggered in slowmo
      if (cinemaState.shotFrame === 1) {
        triggerEventEffects(cinemaState, shot.event);
        // Zoom-punch: snap zoom in over target for ~14 frames of faster lerp.
        // lead_change/comeback have no physical location so skip the punch.
        const et = shot.event && shot.event.type;
        if (et && et !== 'lead_change' && et !== 'comeback') {
          const punchMul = (et === 'final_capture' || et === 'edge_out') ? 1.18 : 1.22;
          cinemaState.cam.zoom = cinemaState.cam.tzoom * punchMul;
          cinemaState.cam.punchTtl = 14;
        }
      }
      // Start fading out the edge-flash border partway through the hold.
      if (shot.event && shot.event.type === 'edge_out' &&
          cinemaState.shotFrame === Math.floor(shot.holdFrames * 0.4)) {
        cinemaState.edgeFlashTarget = 0;
      }
      if (cinemaState.shotFrame >= shot.holdFrames) advanceShot();
      break;
    }
    case 'final': {
      cinemaState.stepFloat = frames.length - 1;
      if (cinemaState.shotFrame >= shot.duration) advanceShot();
      break;
    }
    case 'multi': {
      // Phases: split-in (0..splitInFrames) -> slowmo through events ->
      // hold -> split-out. drawCinemaFrame uses shot._cams via materializePanes.
      const slowmoFrames = Math.ceil((shot.toStep - shot.fromStep) / shot.slowmoStepsPerFrame);
      const totalFrames = shot.splitInFrames + slowmoFrames + shot.holdFrames + shot.splitOutFrames;
      const f = cinemaState.shotFrame;
      if (f === 1) {
        cinemaState.stepFloat = Math.max(cinemaState.stepFloat, shot.fromStep);
        for (const p of shot.panes) p._triggered = false;
      }
      if (f <= shot.splitInFrames) {
        // Split-in: panes hold their time at fromStep; cameras lerp from wide
        // (initial) toward their event-focused targets (set on each pane).
        for (const p of shot.panes) p._curTarget = p.target;
        cinemaState.stepFloat = shot.fromStep;
      } else if (f <= shot.splitInFrames + slowmoFrames) {
        // Slowmo phase: advance time, fire events as their t is crossed.
        cinemaState.stepFloat += shot.slowmoStepsPerFrame;
        for (const p of shot.panes) {
          if (!p._triggered && cinemaState.stepFloat >= p.event.t) {
            p._triggered = true;
            const capBefore = cinemaState.captions.slice();
            triggerEventEffects(cinemaState, p.event);
            if (!shot.allSameType) {
              // Mixed types: relocate the just-pushed caption into per-pane.
              if (cinemaState.captions.length > 0) {
                const c = cinemaState.captions[0];
                cinemaState.paneCaptions = cinemaState.paneCaptions || [];
                cinemaState.paneCaptions.push({ ...c, paneIdx: shot.panes.indexOf(p) });
                cinemaState.captions = capBefore;
              }
            }
          }
        }
      } else if (f <= shot.splitInFrames + slowmoFrames + shot.holdFrames) {
        // Hold phase: freeze at toStep.
        cinemaState.stepFloat = shot.toStep;
        // For same-type clusters, fire one aggregate caption on first hold frame.
        if (shot.allSameType && !shot._aggFired) {
          shot._aggFired = true;
          const ev0 = shot.events[0];
          const label = aggregateCaptionLabel(ev0.type);
          const col = aggregateCaptionColor(shot.events);
          pushCaption(cinemaState, `${shot.events.length}× ${label}`,
            shot.events.length === 4 ? 'CHAOS' :
            shot.events.length === 3 ? 'TRIPLE EVENT' : 'DOUBLE EVENT', col, 220);
        }
      } else {
        // Split-out phase: cameras lerp back to wide; time stays at toStep.
        for (const p of shot.panes) p._curTarget = { cx: 50, cy: 50, zoom: 0.78 };
      }
      if (f >= totalFrames) advanceShot();
      break;
    }
  }
  drawCinemaFrame();
  cinemaRAF = requestAnimationFrame(cinemaTick);
}

// Draw inter-pane borders + a thin glow line. Easing in/out so split-in and
// split-out feel like a real "snap into halves" + "merge back" rather than a
// hard cut. We derive the border alpha from shot phase.
function drawPaneBorders(panes, W, H, shot) {
  // Compute border alpha based on shot phase (1 during hold, lerping 0..1 in
  // split-in, lerping 1..0 in split-out).
  const f = cinemaState.shotFrame;
  const slowmoFrames = Math.ceil((shot.toStep - shot.fromStep) / shot.slowmoStepsPerFrame);
  let a = 1;
  if (f <= shot.splitInFrames) a = f / shot.splitInFrames;
  else if (f > shot.splitInFrames + slowmoFrames + shot.holdFrames) {
    const outStart = shot.splitInFrames + slowmoFrames + shot.holdFrames;
    a = Math.max(0, 1 - (f - outStart) / shot.splitOutFrames);
  }
  if (a <= 0.02) return;
  // Find all unique X and Y border lines between pane rects.
  const xs = new Set(), ys = new Set();
  for (const p of panes) {
    if (p.x > 0) xs.add(p.x);
    if (p.x + p.w < W) xs.add(p.x + p.w);
    if (p.y > 0) ys.add(p.y);
    if (p.y + p.h < H) ys.add(p.y + p.h);
  }
  const lw = Math.max(2, Math.min(W, H) * 0.004);
  bctx.save();
  // Dark inner bar
  bctx.fillStyle = `rgba(8, 12, 24, ${a * 0.95})`;
  for (const x of xs) bctx.fillRect(x - lw, 0, lw * 2, H);
  for (const y of ys) bctx.fillRect(0, y - lw, W, lw * 2);
  // Bright outer hairline
  bctx.strokeStyle = `rgba(255, 220, 130, ${a * 0.85})`;
  bctx.lineWidth = 1.5;
  bctx.shadowColor = `rgba(255, 200, 100, ${a * 0.8})`;
  bctx.shadowBlur = 8;
  bctx.beginPath();
  for (const x of xs) { bctx.moveTo(x + 0.5, 0); bctx.lineTo(x + 0.5, H); }
  for (const y of ys) { bctx.moveTo(0, y + 0.5); bctx.lineTo(W, y + 0.5); }
  bctx.stroke();
  bctx.shadowBlur = 0;
  // Corner accent pips at the centre intersection (if any)
  if (xs.size > 0 && ys.size > 0) {
    bctx.fillStyle = `rgba(255, 230, 150, ${a})`;
    for (const x of xs) for (const y of ys) {
      bctx.beginPath(); bctx.arc(x, y, lw * 1.5, 0, PI2); bctx.fill();
    }
  }
  bctx.restore();
}

// Draw per-pane captions inside the pane rect, near the bottom of the pane.
function drawPaneCaptions(paneCaptions, shot, W, H) {
  if (!shot.panes) return;
  const liveCams = shot._cams || [];
  const liveRects = shot.panes.map((p, i) => ({
    x: Math.floor(p.fracX * W), y: Math.floor(p.fracY * H),
    w: Math.ceil(p.fracW * W), h: Math.ceil(p.fracH * H),
  }));
  for (const pc of paneCaptions) {
    const r = liveRects[pc.paneIdx];
    if (!r) continue;
    const a = (pc.age < 10) ? pc.age / 10 :
              (pc.age > pc.life - 20 ? (pc.life - pc.age) / 20 : 1);
    if (a <= 0) continue;
    const titleFs = Math.min(36, Math.floor(r.w * 0.045));
    const subFs = Math.min(16, Math.floor(r.w * 0.022));
    const boxH = titleFs * (pc.sub ? 2.0 : 1.5);
    const boxBottom = r.y + r.h * 0.88;
    const boxTop = boxBottom - boxH;
    const titleY = boxTop + titleFs * 0.75;
    const subY = titleY + titleFs * 0.95;
    bctx.globalAlpha = a;
    bctx.fillStyle = 'rgba(0,0,0,0.65)';
    bctx.fillRect(r.x + 4, boxTop, r.w - 8, boxH);
    bctx.font = `bold ${titleFs}px sans-serif`;
    bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
    bctx.fillStyle = pc.color;
    bctx.fillText(pc.text, r.x + r.w / 2, titleY);
    if (pc.sub) {
      bctx.font = `${subFs}px sans-serif`;
      bctx.fillStyle = '#fff';
      bctx.fillText(pc.sub, r.x + r.w / 2, subY);
    }
    bctx.globalAlpha = 1;
  }
}

function aggregateCaptionLabel(type) {
  switch (type) {
    case 'flip': return 'CAPTURES';
    case 'final_capture': return 'FINAL BLOWS';
    case 'edge_out': return 'INTO THE VOID';
    case 'impact': return 'IMPACTS';
    case 'sun_death': return 'SUN DEATHS';
    case 'comet_sweep': return 'COMET SWEEPS';
    case 'comet_spawn': return 'COMETS INCOMING';
    case 'big_fleet': return 'MASSIVE ASSAULTS';
    case 'lead_change': return 'LEAD CHANGES';
    case 'comeback': return 'COMEBACKS';
    case 'player_eliminated': return 'PLAYERS ELIMINATED';
    case 'failed_assault': return 'ASSAULTS REPELLED';
    default: return 'EVENTS';
  }
}

function aggregateCaptionColor(events) {
  // Pick the highest-importance event's color if it has one; else white.
  const sorted = events.slice().sort((a, b) => b.importance - a.importance);
  for (const e of sorted) {
    if (e.owner !== undefined && e.owner >= 0) return COLORS[e.owner];
    if (e.toOwner !== undefined && e.toOwner >= 0) return COLORS[e.toOwner];
    if (e.toPlayer !== undefined && e.toPlayer >= 0) return COLORS[e.toPlayer];
  }
  return '#ffd966';
}

function startCinema() {
  cinemaMode = true;
  cinemaBtn.textContent = 'Exit Cinema';
  cinemaBtn.style.background = '#a13bb8';
  document.body.classList.add('cinema-active');
  cinemaState = buildCinema();
  // Apply first shot's target
  const s0 = cinemaState.shots[0];
  if (s0?.target) {
    cinemaState.cam.cx = cinemaState.cam.tcx = s0.target.cx;
    cinemaState.cam.cy = cinemaState.cam.tcy = s0.target.cy;
    cinemaState.cam.zoom = cinemaState.cam.tzoom = s0.target.zoom;
  }
  // Force a layout pass so the hype canvas gets sized correctly.
  requestAnimationFrame(() => resizeCanvases());
  if (cinemaRAF) cancelAnimationFrame(cinemaRAF);
  cinemaRAF = requestAnimationFrame(cinemaTick);
}

function stopCinema() {
  cinemaMode = false;
  if (cinemaRAF) cancelAnimationFrame(cinemaRAF);
  cinemaRAF = null;
  cinemaBtn.textContent = 'Cinema';
  cinemaBtn.style.background = '#7a2b8c';
  document.body.classList.remove('cinema-active');
  cinemaState = null;
  // Force a synchronous reflow so getBoundingClientRect on the next resize
  // reads the post-cinema grid layout, not the leftover full-viewport one.
  // (Reading offsetHeight is the classic "flush layout" trick.)
  void document.body.offsetHeight;
  resizeCanvases();
  draw();
  // Belt-and-braces: schedule another resize next frame in case any deferred
  // CSS / layout pass shifts things again.
  requestAnimationFrame(() => {
    if (cinemaMode) return;
    resizeCanvases();
    draw();
  });
}

cinemaBtn.onclick = () => {
  if (cinemaMode) stopCinema(); else startCinema();
};
document.getElementById('cinemaExit').onclick = stopCinema;
window.addEventListener('keydown', (e) => {
  if (cinemaMode && e.key === 'Escape') { stopCinema(); e.preventDefault(); }
});

// Auto-start if Python baked the flag in, or URL has #cinema/?cinema=1.
const autoCinema =
  DATA.autoCinema === true ||
  location.hash.includes('cinema') || location.hash.includes('ultra') ||
  new URLSearchParams(location.search).get('cinema') === '1' ||
  new URLSearchParams(location.search).get('ultra') === '1';

buildTabs();
loadMatch(0);
resizeCanvases();
if (autoCinema) startCinema();
</script>
</body></html>
"""
